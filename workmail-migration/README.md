# Amazon WorkMail -> Zoho Mail Lite

WorkMail shuts down on **31 March 2027**. After that, mailboxes can't be reached.
Scope: one mailbox (me@zanekirk.xyz, ~3.4 GB); DNS in Route 53 (us-east-1);
destination Zoho Mail Lite. Pick the 10 GB tier: the 5 GB tier leaves little headroom.

Setup: `pip install awscli boto3`, with AWS credentials allowed by `iam-policy.json`
and `AWS_DEFAULT_REGION=us-east-1`.

## Steps

| # | Step | Who |
|---|------|-----|
| 1 | Sign up for Zoho Mail Lite with "Sign up with a domain I already own": zanekirk.xyz. Note which Zoho data center you're on (the URL: zoho.com, zoho.eu, zoho.com.au ...). | You |
| 2 | Verify the domain: choose the TXT method and copy the `zoho-verification=zb....zmverify.zoho.com` value. Then `python3 dns_cutover.py zanekirk.xyz verify --txt VALUE --apply`, and click Verify in Zoho. | Claude, then you |
| 3 | Create the user me@zanekirk.xyz in Zoho (or make it the admin account during signup). | You |
| 4 | Back up the WorkMail mailbox to S3: `./backup_mailbox.sh`. Optional safety net. | Claude |
| 5 | Copy mail: Zoho Admin Console -> Data Migration -> Create. Server `imap.mail.us-east-1.awsapps.com`, port 993, SSL. Add the user with source username me@zanekirk.xyz, your WorkMail password, destination me@zanekirk.xyz. | You |
| 6 | Check that mail arrived in Zoho. | You |
| 7 | DKIM: Zoho Admin Console -> Domains -> zanekirk.xyz -> Email configuration -> DKIM -> Add, pick a selector name, copy the TXT value. | You |
| 8 | Switch mail to Zoho: `python3 dns_cutover.py zanekirk.xyz cutover --zoho-dc com --dkim-selector SELECTOR --dkim "v=DKIM1; k=rsa; p=..."` (dry run), compare MX/SPF with Zoho's Email configuration page, then add `--apply`. Click Verify for MX, SPF and DKIM in Zoho. | Claude, then you |
| 9 | Run the data migration again (or "retry") to pick up mail that arrived in WorkMail during the switch. | You |
| 10 | Calendar and contacts: export from the WorkMail web app or Outlook as .ics/.vcf, import into Zoho Calendar/Contacts. | You |
| 11 | Set up phone and desktop mail apps with Zoho (Zoho Mail app, or IMAP). | You |
| 12 | After a few weeks: `python3 dns_cutover.py zanekirk.xyz cleanup --apply`, then delete the WorkMail organization in the AWS console and delete the `claude-migration` access key. | Claude, then you |

## Rollback

Every `--apply` first saves the zone to `inventory/route53-zanekirk.xyz-<time>.json`.
`python3 dns_cutover.py zanekirk.xyz restore inventory/<file>.json --apply` puts those records back.

## Safety

The IAM policy cannot delete the WorkMail organization, reset passwords, or touch any
S3 bucket except `workmail-backup-*`.
