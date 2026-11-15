"""English interface text. Keys marked js.capture. are passed to static/js/capture.js."""

MESSAGES = {
    "nav.menu": "Menu",
    "nav.capture": "Capture",
    "nav.switch": "switch",
    "foot.account": "Account",
    "foot.privacy": "Privacy",
    "foot.terms": "Terms",
    "foot.source": "Source code",
    "foot.sign_out": "Sign out",
    "foot.disclaimer": (
        "Ledgerline is bookkeeping software, not financial or tax advice. "
        "Check figures before filing with the Belastingdienst."
    ),
    "lang.label": "Language",
    "role.owner": "owner",
    "role.bookkeeper": "bookkeeper",
    "role.accountant": "accountant",
    "role.uploader": "uploader",
    "privacy.photo_reading": (
        "Photo reading is on: photos and scans are sent to an external service to be read. "
        "Do not upload payslips, bank statements or other documents with personal data as "
        "photos or scans. Upload those as PDF with a text layer, where they are recognised "
        "and never sent."
    ),
    "password.too_short": "Password must be at least {min} characters.",
    "password.mismatch": "The two passwords do not match.",
    "password.repeated": "Password cannot be one repeated character.",
    "password.is_email": "Password cannot be your email address.",
    "auth.email": "Email",
    "auth.password": "Password",
    "auth.back_to_login": "Back to sign in",
    "auth.forgot_link": "Forgot password?",
    "auth.login.title": "Sign in",
    "auth.login.intro": "Sign in to your books.",
    "auth.login.submit": "Sign in",
    "auth.login.cookies": "Only functional cookies are used.",
    "auth.login.too_many": "Too many attempts. Try again in 15 minutes.",
    "auth.login.wrong": "Wrong email or password.",
    "auth.login.no_workspace": (
        "Your account is not a member of any workspace yet. Ask the owner of the books to add you."
    ),
    "auth.forgot.title": "Forgot password",
    "auth.forgot.intro": (
        "Enter your email address. If an account exists, we send a link to choose a new password."
    ),
    "auth.forgot.submit": "Send link",
    "auth.forgot.notice": (
        "If an account exists for that address, we sent a link to reset the password."
    ),
    "auth.reset.title": "Choose a new password",
    "auth.reset.intro": "Choose a new password. Other devices are signed out.",
    "auth.reset.submit": "Set password",
    "auth.reset.done": "Password changed. Sign in with the new password.",
    "auth.new_password": "New password",
    "auth.repeat_new_password": "Repeat new password",
    "auth.password_hint": "At least 12 characters.",
    "auth.link_invalid.title": "Link not valid",
    "auth.link_invalid.body": "This link is not valid. It may have expired or been used already.",
    "auth.invite.title": "Accept invitation",
    "auth.invite.intro": "You are invited to work in {tenant} as {email}.",
    "auth.invite.choose_password": "Choose a password",
    "auth.invite.repeat_password": "Repeat password",
    "auth.invite.submit": "Accept invitation",
    "auth.invite.done": "Invitation accepted. Sign in to continue.",
    "auth.totp.title": "Second step",
    "auth.totp.intro": "Enter the 6-digit code from your authenticator app, or a recovery code.",
    "auth.totp.code": "Code",
    "auth.totp.submit": "Verify",
    "auth.totp.restart": "Start over",
    "auth.totp.invalid": "That code is not valid.",
    "auth.totp.too_many": "Too many wrong codes. Sign in again.",
    "auth.tenants.title": "Choose a workspace",
    "auth.tenants.intro": "Choose the account you want to work in.",
    "auth.tenants.other": "Choose another workspace",
    "auth.noadmin.owner_title": "Create your books",
    "auth.noadmin.owner_intro": (
        "Your account has no administration yet. Create the first one to start booking."
    ),
    "auth.noadmin.name": "Name",
    "auth.noadmin.legal_form": "Legal form",
    "auth.noadmin.submit": "Create your books",
    "auth.noadmin.title": "No access yet",
    "auth.noadmin.body": "You don't have access to any administration yet.",
    "auth.noadmin.hint": (
        "Ask the owner of your account to give you access. You can sign in again once they have."
    ),
    "capture.title": "Capture",
    "capture.heading": "Capture a document",
    "capture.take_photo": "Take photo",
    "capture.take_photo_hint": "Opens the camera. Lay the receipt flat in good light.",
    "capture.choose_file": "Choose file",
    "capture.choose_file_hint": "PDF, JPEG or PNG, up to {mb} MB each.",
    "capture.note": "Note (optional)",
    "capture.note_placeholder": "e.g. Fuel, client lunch",
    "capture.submit": "Upload to inbox",
    "capture.footnote": (
        "Uploads for {admin}. Duplicates are recognised and not stored twice. Without a "
        "connection, uploads wait on this phone and are sent when you are back online."
    ),
    "capture.choose_first": "Choose a photo or file first.",
    "capture.unsupported": "This file type is not supported. Send a PDF, JPEG or PNG.",
    "capture.empty": "The file is empty",
    "capture.too_large": "The file is too large",
    "captured.title": "Uploaded",
    "captured.heading": "Upload result",
    "captured.duplicate": "{name} is already in your inbox.",
    "captured.view": "View it",
    "captured.in_inbox": "In your inbox:",
    "captured.open": "Open document",
    "captured.another": "Capture another",
    "captured.go_inbox": "Go to inbox",
    "inbox.upload.label": "Add invoices (PDF, JPEG, PNG or UBL XML, up to {mb} MB each)",
    "inbox.upload.hint": (
        "Drop files here or choose them. Photos and PDFs are read for you to check; "
        "nothing is booked without your review."
    ),
    "inbox.upload.submit": "Upload",
    "js.capture.sign_in_again_one": "Sign in again to upload {count} item.",
    "js.capture.sign_in_again_other": "Sign in again to upload {count} items.",
    "js.capture.sign_in": "Sign in",
    "js.capture.waiting": "{count} waiting to upload",
    "js.capture.retry": "Retry",
    "js.capture.discard": "Discard",
    "js.capture.sent_one": "{count} queued upload sent. Check your inbox.",
    "js.capture.sent_other": "{count} queued uploads sent. Check your inbox.",
    "js.capture.upload_failed": "Upload failed. Check your connection and try again.",
    "js.capture.saved_offline": (
        "No connection. Saved on this phone; it uploads when you are back online."
    ),
    "js.capture.storage_full": (
        "This phone is out of storage, so the files could not be saved for later. "
        "Free some space and try again."
    ),
    "js.capture.storage_blocked": (
        "This browser cannot save uploads for later, which private browsing often blocks. "
        "Connect to the internet and try again."
    ),
    "js.capture.storage_failed": "The files could not be saved on this phone. Try again.",
    "js.capture.err.admin_gone": (
        "The administration is no longer available. Send it again from the inbox."
    ),
    "js.capture.err.too_large": "The file is too large.",
    "js.capture.err.unsupported": "This file type is not supported. Send a PDF, JPEG or PNG.",
    "js.capture.err.missing_file": "The saved file is missing. Choose it again.",
    "js.capture.err.refused": "The server refused this upload (error {status}).",
    "js.capture.err.kept_failing": "The server kept failing. Try again later.",
    "js.capture.err.read_only": "Your account is read-only here and cannot upload documents.",
}
