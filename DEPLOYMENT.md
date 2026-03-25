# Deployment Guide

## Overview

The app is deployed via **GitHub → Railway**. Every time you push to GitHub,
Railway automatically redeploys. Updates from Claude take 3 commands on your end.

---

## One-time setup

### 1. Create a GitHub repository

1. Go to https://github.com/new
2. Name it `f1-watchability`, set it to Public or Private
3. Don't initialise with a README

Then from your local `f1-watchability` folder:

```bash
cd f1-watchability
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/f1-watchability.git
git push -u origin main
```

### 2. Deploy to Railway

1. Go to https://railway.app and sign up (free tier is fine)
2. Click **New Project → Deploy from GitHub repo**
3. Select your `f1-watchability` repo
4. Railway will detect the `Procfile` and deploy automatically
5. Once deployed, click **Settings → Generate Domain** to get your public URL

That's it — your app is live and accessible from your phone.

---

## Updating the app (after Claude makes changes)

When Claude gives you a new `setup_f1watch.py`:

```bash
# 1. Recreate all files
python3 setup_f1watch.py

# 2. Push to GitHub — Railway auto-deploys
cd f1-watchability
git add .
git commit -m "Update scoring"
git push
```

Railway typically redeploys within 60 seconds of a push.

---

## Cache behaviour

- Scored race weekends are cached to disk in `.cache/`
- The cache is keyed on the race + a hash of `config/weights.yaml`
- **If you change weights**, the cache auto-invalidates — no action needed
- The `.cache/` folder is in `.gitignore` so it's local to each deployment

On Railway, the cache resets whenever the app redeploys (Railway has an
ephemeral filesystem). This is fine — races just get recalculated once on
first request after a deploy, then cached for that deployment's lifetime.

If you want persistent caching across deploys on Railway, upgrade to a paid
plan and attach a Railway Volume, then set the `F1_CACHE_DIR` environment
variable to the volume mount path.

---

## Useful endpoints

| URL | What it does |
|-----|-------------|
| `/` | Main UI |
| `/api/calendar?year=2026` | List race weekends |
| `/api/score?year=2026&meeting_key=XXX` | Score a race weekend |
| `/api/cache` | Show what's cached |
| `/api/cache/clear` (POST) | Clear all cached scores |
