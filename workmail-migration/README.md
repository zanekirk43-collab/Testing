# Amazon WorkMail migration

WorkMail shuts down on **31 March 2027**. After that, mailboxes can't be reached.
Scope: one mailbox; DNS hosted in Route 53.

## Steps

1. **Pick the new provider** and create the mailbox there for the same address.
2. **Give the migration access** (do not paste keys into chat):
   - IAM -> create user `claude-migration`, attach an inline policy with `iam-policy.json`.
   - Create an access key for it.
   - Add `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_DEFAULT_REGION`
     (your WorkMail region) as environment variables in the Claude cloud environment
     settings, then start a new session.
3. **Back up** -> `./backup_mailbox.sh`. Writes an inventory to `inventory/` and a full
   mailbox export to a private, KMS-encrypted S3 bucket `workmail-backup-<account>-<region>`.
4. **Copy mail** with the new provider's IMAP migration tool, using
   `imap.mail.<region>.awsapps.com`, port 993, SSL, and your WorkMail username/password.
   Calendar and contacts: export from Outlook or the WorkMail web app as .ics/.vcf and import.
5. **DNS cutover (Route 53)** - only after the copy is checked: lower TTLs, replace MX,
   SPF, DKIM, DMARC and autodiscover records with the new provider's values, remove the
   WorkMail/SES records, run one last sync.
6. **Check and decommission**: send/receive tests, reconnect devices, then after a few
   weeks delete the WorkMail organization yourself, well before 31 March 2027.

## Safety

The IAM policy cannot delete the WorkMail organization, reset passwords, or touch any
S3 bucket except `workmail-backup-*`. Delete the access key once the migration is done.
