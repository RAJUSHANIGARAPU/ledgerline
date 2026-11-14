# Sending documents from your iPhone

Three ways to get a receipt or invoice into the Ledgerline inbox from a phone. All of them end in the same place: Inbox, source "phone" (or "email").

Ledgerline must be reachable from your phone over HTTPS. The address below is whatever you set as `LEDGERLINE_PUBLIC_BASE_URL`; this guide writes it as `https://ledger.example.com`.

## 1. The web app (camera and files)

1. Open `https://ledger.example.com/capture` in Safari and sign in.
2. Tap Share, then Add to Home Screen, then Add.
3. Open Ledgerline from the home screen. It starts on the capture page.
4. Tap Take photo to use the camera, or Choose file for PDFs and existing photos. Add an optional note and tap Upload to inbox.
5. The result screen says "In your inbox" with a link to the document. Capture another starts over. A file you already sent is reported as a duplicate and not stored twice.

Notes:

- Safari converts HEIC photos to JPEG when you pick them in a file input. Ledgerline accepts PDF, JPEG, PNG and UBL/XML only; anything else gets a clear error.
- If the connection drops while you upload from the capture page, the files are saved on the phone and the page shows "N waiting to upload". They are sent automatically when the phone is back online (and again whenever you open or return to the capture page); each one is cleared only after the server confirms it. A queued file always goes to the administration it was captured for, even if you switch administration in the meantime. A file the server refuses stays in the list with the reason and a Discard button.
- The queue needs the capture page to be open. Opening the app while fully offline shows "You're offline"; reopen it once you have a connection. Only iOS Safari without Background Sync relies on this; browsers that have it can also send in the background.
- The app caches only its own static files (styles, icons), never pages or data.

## 2. iOS Shortcut: "Send to Ledgerline"

The shortcut adds Ledgerline to the Share Sheet for images and PDFs, so you can send a receipt from Photos, Files, Mail or Safari without opening the app.

### Create a token

1. In Ledgerline open Settings, then Phone devices (`/settings/devices`).
2. Enter a name such as "iPhone" and tap Create token.
3. Copy the token (it starts with `ll_`). It is shown once. Only a hash is stored, so it cannot be shown again.

### Build the shortcut

Action names below are from recent iOS versions. Apple renames things between releases; where a label may differ it says so.

1. Open the Shortcuts app, tap + to create a shortcut, and name it "Send to Ledgerline".
2. Open the shortcut's settings (the info button or the name at the top) and turn on Show in Share Sheet. Under accepted input types keep only Images and PDFs (the setting is labelled "Share Sheet Types" or similar). This makes the shortcut receive the shared item as Shortcut Input. Older iOS versions have a first action called "Receive ... input from Share Sheet" instead.
3. Add the action Convert Image (search "convert image"). Set it to convert to JPEG, with the input set to Shortcut Input. This also turns HEIC photos into JPEG. A PDF is not an image, so this action may fail or pass it through unchanged. Easy way to handle both:
   - Add If, condition "Shortcut Input" "Type" "is" "Image". Put Convert Image inside the If branch, and add a Set Variable action named `upload` after it. In the Otherwise branch, add Set Variable `upload` to Shortcut Input. Close with End If.
   - If you only ever share photos, skip the If and use Convert Image alone.
4. Add Get Contents of URL (search "get contents of url").
   - URL: `https://ledger.example.com/api/v1/inbox`
   - Tap the arrow to show options. Method: POST.
   - Headers: add one, key `Authorization`, value `Bearer ll_your_token` (the word Bearer, a space, then the token).
   - Request Body: Form. Add a new field of type File, key `file`, and set its value to the `upload` variable (or the output of Convert Image if you skipped the If).
5. Add Show Notification and set the text to the Contents of URL result. The server replies with JSON such as `{"id": 12, "status": "new", "duplicate": false}`. Use Get Dictionary Value for `duplicate` or `id` if you want a nicer message.
6. Done. Share a photo or PDF, scroll the Share Sheet, and tap "Send to Ledgerline".

If the upload fails:

| Reply | Meaning |
| --- | --- |
| 401 | token mistyped, revoked, or `Bearer ` missing |
| 413 | file larger than the limit (default 10 MB) |
| 415 | not a PDF, JPEG, PNG or XML file |
| 429 | more than 30 uploads a minute with this token |
| 200 with `"duplicate": true` | already in the inbox; nothing new stored |

### Without Shortcuts

Any client can upload with the same API:

```
curl -H "Authorization: Bearer ll_your_token" \
     -F "file=@receipt.pdf" https://ledger.example.com/api/v1/inbox
```

A raw body works too: `POST /api/v1/inbox?filename=receipt.jpg` with the file bytes as the body.

## 3. Email forwarding

If the mailbox intake is configured (the `LEDGERLINE_IMAP_*` settings), forward or send invoices as attachments to the mailbox address. PDFs, JPEG and PNG attachments are picked up every few minutes. Each administration has its own private address, `mailbox+<token>@domain`, shown on the Inbox page and under Settings (owners and bookkeepers see it; the owner can create a new one, after which the old one stops working). With several customers on one server, mail without a valid token is moved to the Rejected folder instead of being delivered. This works from any mail app, with no setup on the phone.

## Security

- A token can only upload documents into one administration. It cannot read, list, edit or delete anything, and it has no session or password.
- Treat it like a password: do not share the shortcut with the token inside it.
- Lost phone or shared shortcut: open Settings, Phone devices, and press Revoke on that token. It stops working immediately. Then create a new token for the replacement device.
- Create, revoke and every upload are recorded in the audit log. The list shows when each token was last used, so an unexpected date is a warning sign.
- Uploads are rate limited to 30 per minute per token, in memory per server process.
- Use HTTPS only. Over plain HTTP the token travels in the clear.
