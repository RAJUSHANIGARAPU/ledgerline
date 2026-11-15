"""Nederlandse interfacetekst; dezelfde sleutels en plaatsaanduidingen als en.py."""

MESSAGES = {
    "nav.menu": "Menu",
    "nav.capture": "Factuur uploaden",
    "nav.switch": "wisselen",
    "foot.account": "Account",
    "foot.privacy": "Privacy",
    "foot.terms": "Voorwaarden",
    "foot.source": "Broncode",
    "foot.sign_out": "Uitloggen",
    "foot.disclaimer": (
        "Ledgerline is boekhoudsoftware en geen financieel of fiscaal advies. "
        "Controleer de cijfers voordat je aangifte doet bij de Belastingdienst."
    ),
    "lang.label": "Taal",
    "role.owner": "eigenaar",
    "role.bookkeeper": "boekhouder",
    "role.accountant": "accountant",
    "role.uploader": "alleen uploaden",
    "privacy.photo_reading": (
        "Fotoherkenning staat aan: foto's en scans worden naar een externe dienst gestuurd om "
        "te worden uitgelezen. Upload geen loonstroken, bankafschriften of andere documenten "
        "met persoonsgegevens als foto of scan. Upload die als pdf met tekstlaag: die worden "
        "herkend en nooit verstuurd."
    ),
    "password.too_short": "Het wachtwoord moet minimaal {min} tekens bevatten.",
    "password.mismatch": "De twee wachtwoorden komen niet overeen.",
    "password.repeated": "Het wachtwoord mag niet uit één herhaald teken bestaan.",
    "password.is_email": "Het wachtwoord mag niet je e-mailadres zijn.",
    "auth.email": "E-mailadres",
    "auth.password": "Wachtwoord",
    "auth.back_to_login": "Terug naar inloggen",
    "auth.forgot_link": "Wachtwoord vergeten?",
    "auth.login.title": "Inloggen",
    "auth.login.intro": "Log in op je administratie.",
    "auth.login.submit": "Inloggen",
    "auth.login.cookies": "Er worden alleen functionele cookies gebruikt.",
    "auth.login.too_many": "Te veel pogingen. Probeer het over 15 minuten opnieuw.",
    "auth.login.wrong": "Onjuist e-mailadres of wachtwoord.",
    "auth.login.no_workspace": (
        "Je account is nog niet aan een werkruimte toegevoegd. "
        "Vraag de eigenaar van de administratie om je toe te voegen."
    ),
    "auth.forgot.title": "Wachtwoord vergeten",
    "auth.forgot.intro": (
        "Vul je e-mailadres in. Als er een account bij hoort, sturen we je een link om een "
        "nieuw wachtwoord te kiezen."
    ),
    "auth.forgot.submit": "Link versturen",
    "auth.forgot.notice": (
        "Als er een account bij dit e-mailadres hoort, hebben we een link gestuurd om het "
        "wachtwoord opnieuw in te stellen."
    ),
    "auth.reset.title": "Nieuw wachtwoord kiezen",
    "auth.reset.intro": "Kies een nieuw wachtwoord. Op andere apparaten word je uitgelogd.",
    "auth.reset.submit": "Wachtwoord opslaan",
    "auth.reset.done": "Wachtwoord gewijzigd. Log in met je nieuwe wachtwoord.",
    "auth.new_password": "Nieuw wachtwoord",
    "auth.repeat_new_password": "Herhaal nieuw wachtwoord",
    "auth.password_hint": "Minimaal 12 tekens.",
    "auth.link_invalid.title": "Link niet geldig",
    "auth.link_invalid.body": "Deze link is niet geldig. Hij is verlopen of al gebruikt.",
    "auth.invite.title": "Uitnodiging accepteren",
    "auth.invite.intro": "Je bent uitgenodigd om te werken in {tenant} als {email}.",
    "auth.invite.choose_password": "Kies een wachtwoord",
    "auth.invite.repeat_password": "Herhaal wachtwoord",
    "auth.invite.submit": "Uitnodiging accepteren",
    "auth.invite.done": "Uitnodiging geaccepteerd. Log in om verder te gaan.",
    "auth.totp.title": "Tweede stap",
    "auth.totp.intro": (
        "Vul de 6-cijferige code uit je authenticator-app in, of een herstelcode."
    ),
    "auth.totp.code": "Code",
    "auth.totp.submit": "Controleren",
    "auth.totp.restart": "Opnieuw beginnen",
    "auth.totp.invalid": "Deze code is niet geldig.",
    "auth.totp.too_many": "Te veel onjuiste codes. Log opnieuw in.",
    "auth.tenants.title": "Kies een werkruimte",
    "auth.tenants.intro": "Kies het account waarin je wilt werken.",
    "auth.tenants.other": "Andere werkruimte kiezen",
    "auth.noadmin.owner_title": "Maak je administratie aan",
    "auth.noadmin.owner_intro": (
        "Je account heeft nog geen administratie. Maak de eerste aan om te beginnen met boeken."
    ),
    "auth.noadmin.name": "Naam",
    "auth.noadmin.legal_form": "Rechtsvorm",
    "auth.noadmin.submit": "Administratie aanmaken",
    "auth.noadmin.title": "Nog geen toegang",
    "auth.noadmin.body": "Je hebt nog geen toegang tot een administratie.",
    "auth.noadmin.hint": (
        "Vraag de eigenaar van je account om je toegang te geven. "
        "Daarna kun je opnieuw inloggen."
    ),
    "capture.title": "Factuur uploaden",
    "capture.heading": "Factuur of bon uploaden",
    "capture.take_photo": "Foto maken",
    "capture.take_photo_hint": "Opent de camera. Leg de bon plat neer, met goed licht.",
    "capture.choose_file": "Bestand kiezen",
    "capture.choose_file_hint": "Pdf, JPEG of PNG, maximaal {mb} MB per bestand.",
    "capture.note": "Opmerking (optioneel)",
    "capture.note_placeholder": "bijv. brandstof, lunch met klant",
    "capture.submit": "Uploaden naar inbox",
    "capture.footnote": (
        "Uploads voor {admin}. Dubbele bestanden worden herkend en niet twee keer opgeslagen. "
        "Zonder verbinding blijven uploads op deze telefoon staan en worden ze verstuurd "
        "zodra je weer online bent."
    ),
    "capture.choose_first": "Kies eerst een foto of bestand.",
    "capture.unsupported": "Dit bestandstype wordt niet ondersteund. Stuur een pdf, JPEG of PNG.",
    "capture.empty": "Het bestand is leeg",
    "capture.too_large": "Het bestand is te groot",
    "captured.title": "Geüpload",
    "captured.heading": "Resultaat van de upload",
    "captured.duplicate": "{name} staat al in je inbox.",
    "captured.view": "Bekijken",
    "captured.in_inbox": "In je inbox:",
    "captured.open": "Document openen",
    "captured.another": "Nog een uploaden",
    "captured.go_inbox": "Naar de inbox",
    "inbox.upload.label": (
        "Facturen toevoegen (pdf, JPEG, PNG of UBL-xml, maximaal {mb} MB per bestand)"
    ),
    "inbox.upload.hint": (
        "Sleep bestanden hierheen of kies ze. Foto's en pdf's worden uitgelezen zodat je ze "
        "kunt controleren; er wordt niets geboekt zonder jouw controle."
    ),
    "inbox.upload.submit": "Uploaden",
    "js.capture.sign_in_again_one": "Log opnieuw in om {count} bestand te uploaden.",
    "js.capture.sign_in_again_other": "Log opnieuw in om {count} bestanden te uploaden.",
    "js.capture.sign_in": "Inloggen",
    "js.capture.waiting": "{count} wacht op upload",
    "js.capture.retry": "Opnieuw proberen",
    "js.capture.discard": "Verwijderen",
    "js.capture.sent_one": "{count} wachtende upload is verstuurd. Controleer je inbox.",
    "js.capture.sent_other": "{count} wachtende uploads zijn verstuurd. Controleer je inbox.",
    "js.capture.upload_failed": (
        "Uploaden mislukt. Controleer je verbinding en probeer het opnieuw."
    ),
    "js.capture.saved_offline": (
        "Geen verbinding. Opgeslagen op deze telefoon; het wordt geüpload zodra je weer "
        "online bent."
    ),
    "js.capture.storage_full": (
        "Deze telefoon heeft geen opslagruimte meer, dus de bestanden konden niet worden "
        "bewaard voor later. Maak ruimte vrij en probeer het opnieuw."
    ),
    "js.capture.storage_blocked": (
        "Deze browser kan uploads niet bewaren voor later, wat privénavigatie vaak blokkeert. "
        "Maak verbinding met internet en probeer het opnieuw."
    ),
    "js.capture.storage_failed": (
        "De bestanden konden niet op deze telefoon worden bewaard. Probeer het opnieuw."
    ),
    "js.capture.err.admin_gone": (
        "De administratie is niet meer beschikbaar. Verstuur het bestand opnieuw vanuit de inbox."
    ),
    "js.capture.err.too_large": "Het bestand is te groot.",
    "js.capture.err.unsupported": (
        "Dit bestandstype wordt niet ondersteund. Stuur een pdf, JPEG of PNG."
    ),
    "js.capture.err.missing_file": "Het opgeslagen bestand ontbreekt. Kies het opnieuw.",
    "js.capture.err.refused": "De server heeft deze upload geweigerd (fout {status}).",
    "js.capture.err.kept_failing": "De server bleef fouten geven. Probeer het later opnieuw.",
    "js.capture.err.read_only": (
        "Je account is hier alleen-lezen en kan geen documenten uploaden."
    ),
}
