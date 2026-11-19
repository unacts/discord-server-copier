# Discord Server Copier

Copies an entire Discord server's structure to another server using the Discord REST API.

## Features

- Copies server name, icon, banner, splash and description
- Copies all roles (permissions, colors, order, icons)
- Copies all channels and categories (topics, slowmode, permission overwrites, forum tags)
- Copies emojis, stickers, welcome screen, widget, scheduled events and bans
- Wipes existing roles/channels/emojis/stickers on the destination before copying (optional)
- Automatic fallbacks for boost-gated features, missing permissions and rate limits
- Clean web UI running on localhost

## What is NOT copied

Messages, members, bots, threads, invite links, boost status and vanity URLs — the Discord API does not allow copying these.

## Requirements

- Python 3.8+
- A Discord account that is a member of both servers, with Administrator (or at least Manage Server, Manage Roles, Manage Channels) on the destination

## Setup

```bash
pip install -r requirements.txt
python discord_copier.py

A browser window opens at http://127.0.0.1:8765.

Getting your token
Open https://discord.com/app in your browser
Press F12 → Application/Storage tab → Local Storage → click https://discord.com
Copy the value of the token key

# Disclaimer
Automating user accounts (self-bots) is against the Discord Terms of Service. Use this tool on servers you own or have explicit permission to modify. You are responsible for any action taken against your account.