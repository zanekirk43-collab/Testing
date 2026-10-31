# Amazon WorkMail -> Google Workspace

WorkMail shuts down on **31 March 2027**. After that, mailboxes can't be reached.
Scope: one mailbox; DNS hosted in Route 53; destination Google Workspace Business Starter.

Setup: `pip install awscli boto3`, with AWS credentials allowed by `iam-policy.json`
and `AWS_DEFAULT_REGION` set to the WorkMail region.

## Steps

| # | Step | Who |
|---|------|-----|
| 1 | Sign up for Google Workspace with your existing domain; make the admin account your current address. | You |
| 2 | Verify the domain: copy the `google-site-verification=...` token from the signup screen, then `python3 dns_cutover.py DOMAIN verify --token TOKEN --apply`. | Claude |
| 3 | Back up the WorkMail mailbox to S3: `./backup_mailbox.sh`. | Claude |
| 4 | Copy mail: Admin console -> Data -> Data import & export -> Data migration. Source "Other IMAP server", server `imap.mail.<region>.awsapps.com`, port 993, SSL. Sign in with your WorkMail address and password. | You |
| 5 | Check that mail arrived in Gmail. | You |
| 6 | Generate DKIM: Admin console -> Apps -> Google Workspace -> Gmail -> Authenticate email -> Generate new record (2048-bit). Copy the TXT value. | You |
| 7 | Switch mail to Google: `python3 dns_cutover.py DOMAIN cutover --dkim "v=DKIM1; k=rsa; p=..."` (dry run), then add `--apply`. Then click "Start authentication" for DKIM in the Admin console. | Claude |
| 8 | Run the data migration once more to pick up anything that arrived in WorkMail during the switch. | You |
| 9 | Calendar and contacts: export from the WorkMail web app or Outlook as .ics/.vcf, import into Google Calendar/Contacts. | You |
| 10 | Reconnect phone and desktop mail apps to your Google account. | You |
| 11 | After a few weeks: `python3 dns_cutover.py DOMAIN cleanup --apply`, then delete the WorkMail organization in the AWS console. | Claude, then you |

## Rollback

Every `--apply` first saves the zone to `inventory/route53-DOMAIN-<time>.json`.
`python3 dns_cutover.py DOMAIN restore inventory/<file>.json --apply` puts those records back.

## Safety

The IAM policy cannot delete the WorkMail organization, reset passwords, or touch any
S3 bucket except `workmail-backup-*`. Delete the access key once the migration is done.
