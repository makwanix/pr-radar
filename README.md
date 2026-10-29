# PR Radar watcher

A free, hourly watcher for the official Canadian immigration pages that matter to my PR plan. It runs on GitHub Actions and uses no AI and no paid services.

## What it does

Every hour (at about :17) it:

1. Reads 10 official pages from IRCC and Ontario (listed in `sources.json`) and compares their text with the last saved copy.
2. Reads the data file behind IRCC's Express Entry rounds page and reports each new draw (date, type, invitations, lowest score).
3. Reads the IRCC newsroom feed and looks for new items about Express Entry, PR, study and work permits, PGWP, provincial nominees and fees.
4. If anything changed, it **opens a GitHub issue** listing the new and removed text, with links. GitHub emails you and sends a notification to the GitHub mobile app.
5. Saves the new copy so the same change isn't reported twice.

It **does not interpret** changes. To find out what a change means for you and update your Canada PR Intelligence app, ask Claude: “PR Radar found a change — what does it mean for me?” and paste the issue link.

If a page can't be read for 3 runs in a row, it opens an issue saying so. It never makes up an update.

## Setup (about 5 minutes)

1. The files are in this repository; nothing to install.
2. Go to **Settings → Actions → General → Workflow permissions** and choose **Read and write permissions**. Save.
3. Go to the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
4. Click **PR Radar watcher → Run workflow**. In about a minute you'll get an issue titled “[PR Radar] Watcher is running”. That first run only saves starting copies.
5. Make sure you get notified: open the repo's **Watch** menu and pick **All Activity** (or at least **Issues**). Install the **GitHub mobile app** for phone notifications.

### Optional: instant phone push with ntfy (free)

1. Install the **ntfy** app (Android or iPhone).
2. In the app, subscribe to a long, hard-to-guess topic name, for example `pr-radar-akshay-7f3k9q2x`. Anyone who knows the name can read the alerts, so don't use something obvious.
3. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**. Name: `NTFY_TOPIC`. Value: your topic name.

## Cost

Free. A private repository on GitHub Free includes 2,000 Actions minutes a month. Each run bills as 1 minute, so hourly checks use about 720–745 minutes a month.

## Changing what it watches

Edit `sources.json`. Add a page with an `id`, `name` and `url`. Remove one by deleting its entry. The next run saves a starting copy of any new page.

## Limits

- Draws are read from the data file behind IRCC's rounds page. That address isn't documented by IRCC; if it changes, you'll get an issue saying the source is unreachable.
- Small website layout edits can occasionally trigger a change report with no real rule change. The issue shows the exact text so you can judge.
- GitHub can start scheduled runs several minutes late at busy times.
