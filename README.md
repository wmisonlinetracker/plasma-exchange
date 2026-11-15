# Plasma Exchange

PLASCOM's private competitor price tracker. Every 6 hours, GitHub checks UK competitors' and eBay prices for your plasma parts and updates a **password-protected** dashboard. The dashboard lives at:

**https://wmisonlinetracker.github.io/plasma-exchange/**

## How the login works
- Every scan's results are **encrypted with your password** (AES-256) before anything is saved or published. That covers the dashboard data, the daily roundup and the price history in `data/market.db.enc`.
- The project and website are public, but without the password they only contain scrambled data.
- The website asks for the password and unlocks the data **inside your browser**. The password itself is never sent anywhere.
- **Keep me signed in** remembers you on that computer. **Log out** forgets you.
- **Use a long password**, for example four random words and a number (`copper fence rocket 4721`). Short passwords can be guessed.
- **Keep a note of it.** If you forget it, there is no reset; you would set a new one and start the price history afresh.

## One-time setup
1. **Set the password.** Go to **Settings → Secrets and variables → Actions → New repository secret**.
   - Name: `DASHBOARD_PASSWORD`
   - Secret: your chosen password (at least 10 characters)
2. **Turn on the website.** Go to **Settings → Pages → Build and deployment → Source**, choose **GitHub Actions**.
3. **Run the first scan.** Go to **Actions → Scan competitor prices and update website → Run workflow**. It takes about 30–60 minutes. After that it runs by itself every 6 hours.
4. **eBay (optional).** Get a free Production keyset at developer.ebay.com/my/keys, then add two more secrets:
   - `EBAY_CLIENT_ID`, containing your App ID
   - `EBAY_CLIENT_SECRET`, containing your Cert ID

   Also add a **variable** (Variables tab) named `EBAY_EXCLUDE_SELLERS` containing your own eBay username or usernames, so your listings are ignored.

## Changing the password
Change the `DASHBOARD_PASSWORD` secret, then delete `data/market.db.enc` from the project. The old history can't be opened with the new password, so the history starts again from the next scan.

## Day to day
- **Instant scan:** go to **Actions → Run workflow**.
- **Pricing rules** (alert %, discount vs genuine, undercut amount): edit `app/config.json` using the pencil icon on GitHub. Don't put cost prices in it, because that file is public.
- **Run it on your own PC instead:** download the project and double-click `START HERE (run on my PC).bat`.

## Using your own domain later
1. Go to **Settings → Pages → Custom domain** and enter the domain.
2. In GoDaddy's DNS, add four **A** records for `@`: `185.199.108.153`, `185.199.109.153`, `185.199.110.153` and `185.199.111.153`.
3. Point the `www` CNAME record to `wmisonlinetracker.github.io`.
