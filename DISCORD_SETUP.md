# Setting up the Bet Builder Discord bot

The bot answers in your Discord server while its program runs on your Windows PC
(minimized is fine). Steps 1-3 work from your phone; step 4 needs the PC.

## 1. Create the bot (phone or PC)

1. In a browser, go to <https://discord.com/developers/applications> and sign in
   with your Discord account. On a phone, switch the browser to its desktop view if
   the page looks cramped.
2. Tap **New Application**, name it (e.g. *Bet Builder*), tick the box to agree,
   and tap **Create**.

## 2. Save the token (phone or PC)

1. Open the menu, then **Bot**, then **Reset Token**, then **Yes, do it!**, then **Copy**.
2. Keep it in a private note. It's the bot's password: never post it or send it
   to anyone (Claude included).
3. Leave the "Privileged Gateway Intents" switches off; the bot doesn't need them.

## 3. Add the bot to your server (phone or PC)

1. Open **General Information** and copy the **Application ID**. This number is
   public, not the token, so it's safe to share.
2. Open this link with your Application ID in place of `YOUR_ID`:

   `https://discord.com/oauth2/authorize?client_id=YOUR_ID&permissions=84992&scope=bot%20applications.commands`

   (Or send the Application ID to Claude for a ready-made link.)
3. Pick your server and tap **Authorize**. You need to be the server's owner or an
   admin. The bot shows up offline until step 4.

## 4. Start the bot (Windows PC)

1. Download **BetBuilder-Windows-DiscordBot.exe** from the latest release:
   <https://github.com/levishoaf/Claudecode/releases/latest>
2. Make a folder for it (e.g. *Documents\Bet Builder Bot*), move the file there and
   double-click it. If Windows says "Windows protected your PC", click
   **More info**, then **Run anyway**.
3. When it asks for the token, right-click in its window to paste, then press Enter.
   It's saved as `discord token.txt` in that folder; keep that file private.
4. When it asks to start whenever you sign in to Windows, press Enter (yes).
5. If you skipped step 3, your browser opens the invite page now: pick your server
   and click **Authorize**.
6. In your server, type `/` and the commands appear. In the channel where you want
   results posted, run `/results-here`.

Keep its window open (minimizing is fine) and the PC awake. The bot updates
itself when a new release comes out. Close the window to stop it; to stop it
starting with Windows, run it once with `--startup off`.

## Commands

| Command | What it does |
|---|---|
| `/bets` | Top single bets for a league (NFL, College football, UFC, MLB) |
| `/parlays` | Parlays from one league (P1, P2, ...) |
| `/multilays` | Parlays mixing leagues (X1, X2, ...) |
| `/moneymaker` | Likeliest parlays that pay at least double (M1, M2, ...) |
| `/chance` | Win % for any bet, and whether FanDuel's price is worth it |
| `/place` | Log a bet you placed (`bet:P1`, `bet:M1` or `bet:X1` for a listed parlay) |
| `/remove` | Remove one of your logged bets |
| `/history` | The server's bet history and records |
| `/injuries` | This week's NFL injury report |
| `/results-here` | (Admins) post results in this channel as games finish |

## If something goes wrong

- **"Discord didn't accept that token"**: on the developer site go to **Bot**, then
  **Reset Token**, then **Copy**, and paste the new one when the bot asks.
- **No commands when you type `/`**: make sure the bot's window is open and says
  it's online, then wait a minute and reopen Discord.
- **The bot is offline**: the PC is off or asleep, or the bot's window was closed.
