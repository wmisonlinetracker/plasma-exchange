# Plasma Exchange

This is PLASCOM's competitor price tracker. Every 6 hours, GitHub checks UK competitors' and eBay prices for your plasma parts. It then updates the dashboard on your website.

- **Dashboard** (your domain): watchlist, suggested selling prices, price graph, and an action list of sharp competitor price cuts.
- **Daily roundup:** the *Daily roundup* button on the site. Past roundups are saved in `data/roundups/`.
- **Price history:** stored in `data/market.db` and updated automatically after each scan.

---

## One-time setup

### 1. Put the code on GitHub
1. Sign in at github.com and click **+ → New repository**.
2. Name it `plasma-exchange`. Choose **Public**; see "Privacy" below for why.
3. Tick nothing else, then click **Create repository**.
4. On the new page, click **uploading an existing file**.
5. Open the `plasma-exchange` folder on your PC and select **everything inside it**, including the `.github` folder. Drag it all into the browser, then click **Commit changes**.

### 2. Turn on the website
1. In the repository, go to **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
3. Go to the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
4. You may see one run with a red cross. That run started when you uploaded, before Pages was turned on, so you can ignore it.
5. Click **Scan competitor prices and update website → Run workflow → Run workflow**. The first scan takes about 30–60 minutes.
6. When it shows a green tick, your site is live at `https://YOUR-GITHUB-USERNAME.github.io/plasma-exchange/`.

### 3. Connect your GoDaddy domain
Do these steps in this order. GitHub recommends adding the domain on GitHub before changing DNS.

**a) Verify the domain with GitHub.** This stops anyone else claiming it.
1. Click your profile picture, then **Settings → Pages → Add a domain**. Type your domain, for example `yourdomain.co.uk`.
2. GitHub shows a TXT record: a name like `_github-pages-challenge-yourname` and a long value.
3. In GoDaddy, go to **My Products → (your domain) → DNS**. Click **Add New Record**, choose type **TXT**, and paste the name and value. Save.
4. Back on GitHub, click **Verify**. It can take a few minutes to work.

**b) Tell the repository about the domain.**
- In the repository, go to **Settings → Pages → Custom domain**. Type `yourdomain.co.uk` and click **Save**.

**c) Point the domain at GitHub.** In GoDaddy, go to **DNS → DNS Records**:
1. Delete any existing **A** record with name **@**; GoDaddy's "Parked" record is usually one. Also switch off any **Forwarding** on the domain.
2. Add four **A** records, each with name **@**:

   | Type | Name | Value |
   |---|---|---|
   | A | @ | 185.199.108.153 |
   | A | @ | 185.199.109.153 |
   | A | @ | 185.199.110.153 |
   | A | @ | 185.199.111.153 |

3. Edit or add the **CNAME** record for **www** so it points to `YOUR-GITHUB-USERNAME.github.io`. Use your username only, without the repository name.

**d) Turn on HTTPS.** Usually within an hour (occasionally up to 24 hours), go to **Settings → Pages** and tick **Enforce HTTPS**.

### 4. Switch on eBay prices (optional)
1. Create a free **Production** keyset at developer.ebay.com/my/keys.
2. In the repository, go to **Settings → Secrets and variables → Actions**.
3. On the **Secrets** tab, click **New repository secret** and add:
   - `EBAY_CLIENT_ID`, containing your App ID
   - `EBAY_CLIENT_SECRET`, containing your Cert ID
4. On the **Variables** tab, add `EBAY_EXCLUDE_SELLERS` containing your own eBay username or usernames, separated by commas.

---

## Privacy: please read
GitHub Pages is free for **public** repositories. That means anyone who finds the repository or the website can see:
- the code
- the competitor prices
- your suggested prices

The site tells search engines not to list it, but it isn't password-protected.

- **Don't put cost prices** in `app/config.json` while the repository is public.
- **For a login-protected dashboard:** put the domain behind Cloudflare (free) and turn on Cloudflare Access. Ask Claude to set this up.

## Day to day
- **Instant scan:** go to **Actions → Scan competitor prices… → Run workflow**.
- **Change the rules** (alert %, discount vs genuine, undercut amount): edit `app/config.json` on GitHub using the pencil icon, then commit. The site updates on the next scan.
- **Run it on your own PC instead:** double-click `START HERE (run on my PC).bat`.
