Oracle A1 Hunter

---

Keeps trying to get the strongest Always Free Oracle Cloud instance,
**VM.Standard.A1.Flex with 2 OCPU / 12 GB RAM**. Oracle cut the Always Free
A1 limit from 4 OCPU / 24 GB to this in 2026. The limit is set by `MAX_OCPUS`
and `MAX_MEMORY_GB` at the top of `hunter.py`.

Each run:

- **No A1 instance yet:** tries 2 OCPU, then 1. For each OCPU count it tries
  12 GB RAM down to 2 GB in 2 GB steps (Oracle needs at least 1 GB per OCPU),
  in every Availability Domain, and keeps the first one it gets.
- **A1 instance smaller than 2 OCPU / 12 GB:** tries to resize it up, biggest
  size first, never lowering OCPU or RAM. A successful resize reboots the
  instance.
- **A1 instance at 2 OCPU / 12 GB:** does nothing.

Each run stops after about 4 minutes and starts over on the next run. If the
logs often end with `Out of time`, set `MEMORY_STEP_GB` in `hunter.py` to
4 or 6 so there are fewer sizes to go through.

You get an ntfy push notification whenever it creates or resizes the instance.

It runs on GitHub Actions. The schedule lives in
[`.github/workflows/oracle-a1-hunter.yml`](../../.github/workflows/oracle-a1-hunter.yml)
at the repo root, because GitHub only runs workflows from that folder.

---

## Setup

### 1. In Oracle Cloud (all in your **home region**)

Always Free A1 instances can only be created in your home region (shown at
the top of the console, chosen at sign-up).

1. **Network:** Networking → Virtual Cloud Networks → Start VCN Wizard →
   *Create VCN with Internet Connectivity*. Open the **public** subnet it
   creates and copy its OCID → `OCI_SUBNET_ID`.
2. **Compartment:** Identity → Compartments. Use the root compartment (its
   OCID is your tenancy OCID) unless you made your own → `OCI_COMPARTMENT_ID`.
3. **Image:** pick an **aarch64 (ARM)** image. x86 images will not work on A1.
   Find the OCID for your region on
   [Oracle's image list](https://docs.oracle.com/en-us/iaas/images/), e.g.
   *Canonical-Ubuntu-24.04-aarch64-…* or *Oracle-Linux-9.x-aarch64-…*
   → `OCI_IMAGE_ID`.
4. **API key:** Profile (top right) → My profile → API keys → Add API key →
   *Generate API key pair*. Download the private key, click Add, and copy the
   *Configuration file preview* it shows.

### 2. SSH key (on your own computer)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/oracle_a1
cat ~/.ssh/oracle_a1.pub
```

The `.pub` line goes into `SSH_PUBLIC_KEY`. Keep `~/.ssh/oracle_a1` private;
you'll log in with `ssh -i ~/.ssh/oracle_a1 ubuntu@<public-ip>` (user is
`opc` on Oracle Linux).

### 3. Notifications

Install the ntfy app on your phone and subscribe to a long random topic name
like `a1-hunter-7f3k9q2x`. Anyone who knows the name can read it.

### 4. GitHub

Push this repo to GitHub, then add these under
Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
| --- | --- |
| `OCI_CONFIG` | The configuration file preview from step 1.4 |
| `OCI_KEY` | Full contents of the downloaded private key `.pem` file |
| `OCI_COMPARTMENT_ID` | From step 1.2 |
| `OCI_SUBNET_ID` | From step 1.1 |
| `OCI_IMAGE_ID` | From step 1.3 |
| `SSH_PUBLIC_KEY` | From step 2 |
| `NTFY_TOPIC` | From step 3 |

`OCI_CONFIG` should look like this (the `key_file` line is fixed up
automatically by the workflow):

````
[DEFAULT]
user=ocid1.user...
fingerprint=...
tenancy=ocid1.tenancy...
region=eu-frankfurt-1
key_file=<path to your private keyfile> # TODO
````

### 5. Test it

Actions tab → Oracle A1 Hunter → *Run workflow*. A green run that prints
`Out of host capacity` means everything is set up and it's waiting for
capacity. A red run means a setup problem; open the log to see which one.

After that it runs on its own every 5 minutes.

---

## Notes

- **Public vs private repo:** Actions minutes are free in public repos
  (secrets stay hidden). Private repos get 2,000 free minutes/month, which a
  5-minute schedule uses up in about a week, so switch the cron to
  `*/30 * * * *` there.
- GitHub pauses scheduled workflows in a repo with no commits for 60 days.
  Re-enable it from the Actions tab if that happens.
- Getting capacity can take days or weeks in busy regions. Upgrading the
  account to Pay As You Go is widely reported to make A1 capacity much easier
  to get, and Always Free resources stay free. It needs a card, so set a
  budget alert and only create Always Free resources.
- Some users report that Pay As You Go accounts can still use 4 OCPU / 24 GB
  for free. Oracle hasn't confirmed this and its docs say 2 OCPU / 12 GB, so
  raising `MAX_OCPUS` / `MAX_MEMORY_GB` on such an account may cost money.
- Oracle may reclaim Always Free instances that sit idle for 7 days (Pay As
  You Go accounts are exempt), so keep something running on it.
