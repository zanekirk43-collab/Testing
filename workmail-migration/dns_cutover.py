#!/usr/bin/env python3
"""Route 53 changes for moving a domain from Amazon WorkMail to Google Workspace.

Every command only prints its plan unless you pass --apply. Before any change,
the domain's current records are saved to inventory/ so they can be restored.

  verify   --token TOKEN   add Google's domain-verification TXT record
  cutover  [--dkim VALUE]  point mail at Google: MX, SPF, DKIM, DMARC
  cleanup                  remove leftover WorkMail/SES records
  restore  FILE            put back records from a saved backup (rollback)

Needs: pip install boto3; AWS credentials allowed by iam-policy.json.
"""
import argparse
import datetime
import json
import os
import sys

import boto3

GOOGLE_MX = "1 smtp.google.com"
GOOGLE_SPF = "v=spf1 include:_spf.google.com ~all"
DEFAULT_DMARC = "v=DMARC1; p=none"


def q(text):
    """Quote a TXT value for Route 53, splitting strings over 255 characters."""
    chunks = [text[i:i + 255] for i in range(0, len(text), 255)] or [""]
    return " ".join(f'"{c}"' for c in chunks)


def unq(value):
    """Undo q(): join the quoted strings of one TXT value."""
    parts = value.strip().split('" "')
    return "".join(parts).strip('"')


def find_zone(r53, domain):
    zones = r53.list_hosted_zones_by_name(DNSName=domain)["HostedZones"]
    for z in zones:
        if z["Name"].rstrip(".") == domain and not z["Config"].get("PrivateZone"):
            return z["Id"].split("/")[-1]
    sys.exit(f"No public Route 53 hosted zone found for {domain}")


def all_records(r53, zone_id):
    records = []
    for page in r53.get_paginator("list_resource_record_sets").paginate(HostedZoneId=zone_id):
        records.extend(page["ResourceRecordSets"])
    return records


def get(records, name, rtype):
    for r in records:
        if r["Name"].rstrip(".").lower() == name.lower() and r["Type"] == rtype:
            return r
    return None


def values(rec):
    return [rr["Value"] for rr in rec.get("ResourceRecords", [])] if rec else []


def upsert(name, rtype, vals, ttl):
    return {"Action": "UPSERT", "ResourceRecordSet": {
        "Name": name, "Type": rtype, "TTL": ttl,
        "ResourceRecords": [{"Value": v} for v in vals]}}


def delete(rec):
    return {"Action": "DELETE", "ResourceRecordSet": rec}


def backup(domain, records):
    os.makedirs("inventory", exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = f"inventory/route53-{domain}-{stamp}.json"
    with open(path, "w") as f:
        json.dump(records, f, indent=2)
    return path


def plan_verify(domain, records, token, ttl):
    apex = get(records, domain, "TXT")
    vals = values(apex)
    new = q(f"google-site-verification={token}")
    if new in vals:
        return []
    return [upsert(domain, "TXT", vals + [new], apex["TTL"] if apex else ttl)]


def plan_cutover(domain, records, dkim, dmarc, ttl):
    changes = [upsert(domain, "MX", [GOOGLE_MX], ttl)]

    apex = get(records, domain, "TXT")
    kept = [v for v in values(apex) if not unq(v).startswith("v=spf1")]
    changes.append(upsert(domain, "TXT", kept + [q(GOOGLE_SPF)], ttl))

    if dkim:
        changes.append(upsert(f"google._domainkey.{domain}", "TXT", [q(dkim)], ttl))

    if not get(records, f"_dmarc.{domain}", "TXT"):
        changes.append(upsert(f"_dmarc.{domain}", "TXT", [q(dmarc)], ttl))
    return changes


def plan_cleanup(domain, records):
    changes = []
    for r in records:
        name = r["Name"].rstrip(".").lower()
        vals = " ".join(values(r)).lower()
        if r["Type"] == "CNAME" and name == f"autodiscover.{domain}" and "awsapps.com" in vals:
            changes.append(delete(r))
        elif r["Type"] == "CNAME" and name.endswith(f"._domainkey.{domain}") and "amazonses.com" in vals:
            changes.append(delete(r))
        elif r["Type"] == "TXT" and name == f"_amazonses.{domain}":
            changes.append(delete(r))
    return changes


def plan_restore(domain, records, path):
    with open(path) as f:
        saved = json.load(f)
    changes = []
    for r in saved:
        if r["Type"] in ("NS", "SOA") or "AliasTarget" in r:
            continue
        current = get(records, r["Name"].rstrip("."), r["Type"])
        if current != r:
            changes.append({"Action": "UPSERT", "ResourceRecordSet": r})
    return changes


def show(changes):
    if not changes:
        print("Nothing to change.")
    for c in changes:
        rs = c["ResourceRecordSet"]
        print(f'{c["Action"]:7} {rs["Type"]:5} {rs["Name"]}')
        for v in values(rs):
            print(f"          {v[:110]}{'...' if len(v) > 110 else ''}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("domain")
    p.add_argument("--apply", action="store_true", help="make the changes (default: only show them)")
    p.add_argument("--ttl", type=int, default=300)
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--token", required=True, help="value after google-site-verification=")
    c = sub.add_parser("cutover")
    c.add_argument("--dkim", help="DKIM TXT value from Admin console (v=DKIM1; k=rsa; p=...)")
    c.add_argument("--dmarc", default=DEFAULT_DMARC, help="used only if no _dmarc record exists")
    sub.add_parser("cleanup")
    r = sub.add_parser("restore")
    r.add_argument("file")
    args = p.parse_args()

    domain = args.domain.rstrip(".").lower()
    r53 = boto3.client("route53")
    zone_id = find_zone(r53, domain)
    records = all_records(r53, zone_id)

    if args.cmd == "verify":
        changes = plan_verify(domain, records, args.token, args.ttl)
    elif args.cmd == "cutover":
        changes = plan_cutover(domain, records, args.dkim, args.dmarc, args.ttl)
    elif args.cmd == "cleanup":
        changes = plan_cleanup(domain, records)
    else:
        changes = plan_restore(domain, records, args.file)

    show(changes)
    if not changes or not args.apply:
        if changes:
            print("\nDry run. Re-run with --apply to make these changes.")
        return

    print(f"\nCurrent records saved to {backup(domain, records)}")
    resp = r53.change_resource_record_sets(
        HostedZoneId=zone_id,
        ChangeBatch={"Comment": f"WorkMail -> Google Workspace: {args.cmd}", "Changes": changes})
    change_id = resp["ChangeInfo"]["Id"]
    r53.get_waiter("resource_record_sets_changed").wait(Id=change_id)
    print("Applied and in sync.")


if __name__ == "__main__":
    main()
