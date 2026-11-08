# Verwerkersovereenkomst (template) / Data processing agreement (template)

> **TEMPLATE, NOT LEGAL ADVICE.** This is a starting point written by the operator, not a reviewed legal document. Have it reviewed by a lawyer or privacy adviser before the first signature. Everything in [BRACKETS] must be filled in or deleted. Do not sign a version that still contains brackets or unresolved notes.

## English summary

This agreement is between the operator of the Ledgerline installation (the *processor*, "Verwerker") and the pilot client (the *controller*, "Verwerkingsverantwoordelijke"). The operator hosts the bookkeeping software for the client and processes the client's bookkeeping data on the client's documented instructions only. It covers the elements Article 28(3) of the GDPR (AVG) requires: subject and duration, nature and purpose, types of personal data and data subjects, instructions, confidentiality, security, sub-processors, help with data-subject requests, breach notification, deletion or return at the end, and audits.

The points the client should look at first:

- **Hosting:** Hetzner, in the EU ([HETZNER LOCATION, e.g. Falkenstein or Helsinki]). No other sub-processor unless document extraction is switched on (then Anthropic, see article 6).
- **Breach:** the operator tells the client without undue delay and aims to do so within 24 hours of becoming aware, so the client can meet its own 72-hour deadline towards the Autoriteit Persoonsgegevens.
- **End of the agreement:** the client gets a full export and the operator deletes the data afterwards, except what the law forces the operator to keep. The client stays responsible for its own 7-year fiscal retention duty (it should keep its own export).
- **Liability:** see the pilot agreement.
- Dutch is the binding text; this summary helps reading.

Legal basis: Article 28 of Regulation (EU) 2016/679 ([EUR-Lex](https://eur-lex.europa.eu/eli/reg/2016/679/oj)). Paragraph 3 lists what the contract must contain; the article numbers below point at it.

---

## Verwerkersovereenkomst

**Partijen**

1. **[NAAM CLIENT]**, [RECHTSVORM], KvK [KVK-NUMMER], gevestigd te [ADRES], vertegenwoordigd door [NAAM, FUNCTIE] (hierna: de **Verwerkingsverantwoordelijke**); en
2. **[NAAM EXPLOITANT]**, [RECHTSVORM], KvK [KVK-NUMMER], gevestigd te [ADRES], vertegenwoordigd door [NAAM] (hierna: de **Verwerker**).

Samen: de **Partijen**. Datum ingang: [DATUM].

### Artikel 1. Onderwerp en definities

1.1 De Verwerker stelt de boekhoudsoftware Ledgerline als gehoste dienst beschikbaar aan de Verwerkingsverantwoordelijke, volgens de pilotovereenkomst van [DATUM] (de **Pilotovereenkomst**). Daarbij verwerkt de Verwerker persoonsgegevens namens de Verwerkingsverantwoordelijke.

1.2 Begrippen als persoonsgegevens, verwerken, verwerkingsverantwoordelijke, verwerker en datalek hebben de betekenis uit de Algemene verordening gegevensbescherming (AVG, Verordening (EU) 2016/679).

1.3 Voor de inlogaccounts van gebruikers (e-mailadres, wachtwoord-hash, rol) bepaalt de Verwerker de middelen van de dienst. Voor zover de Verwerker die gegevens voor eigen doeleinden verwerkt (beveiliging, misbruik voorkomen), is hij daarvoor zelf verantwoordelijk, zoals in de privacyverklaring op `/legal/privacy` staat. [CONTROLEER MET JURIST: rolverdeling voor accountgegevens.]

### Artikel 2. Duur (art. 28 lid 3 AVG, onderwerp en duur)

2.1 Deze overeenkomst gaat in op de ingangsdatum en loopt zolang de Verwerker persoonsgegevens verwerkt voor de Verwerkingsverantwoordelijke, en uiterlijk tot de afwikkeling volgens artikel 12.

2.2 Eindigt de Pilotovereenkomst, dan eindigt deze overeenkomst pas nadat artikel 12 is uitgevoerd.

### Artikel 3. Aard en doel van de verwerking

3.1 Aard: opslaan, ordenen, raadplegen, weergeven, exporteren, back-uppen en verwijderen van gegevens in een boekhoudapplicatie; het inlezen van binnenkomende documenten (upload, e-mail, telefoon) en het voorstellen van factuurgegevens.

3.2 Doel: het voeren van de administratie van de Verwerkingsverantwoordelijke, het opstellen van facturen, de btw-aangifte voor te bereiden en bankmutaties te verwerken. Er is geen ander doel.

### Artikel 4. Soorten persoonsgegevens en betrokkenen

| Categorie betrokkenen | Soort gegevens |
|---|---|
| Gebruikers van de Verwerkingsverantwoordelijke (eigenaar, boekhouder, accountant) | naam of e-mailadres, wachtwoord-hash (argon2id), rol, inlog- en auditgegevens (gebruiker, tijdstip, handeling) |
| Klanten en leveranciers die natuurlijke personen zijn (bijvoorbeeld eenmanszaken) | naam, adres, e-mailadres, btw-nummer, KvK-nummer, IBAN, factuur- en betaalgegevens |
| Contactpersonen bij zakelijke klanten en leveranciers | naam, e-mailadres, functie of vermelding op een document |
| Personen op ingelezen documenten en bankmutaties | wat op facturen, bonnen en bankafschriften staat, waaronder tegenpartijnaam, IBAN en omschrijving |

4.2 Er worden geen bijzondere categorieën persoonsgegevens (art. 9 AVG) bewust verwerkt. De Verwerkingsverantwoordelijke laat die niet in de administratie opnemen. Vrije tekstvelden en bijlagen kunnen zulke gegevens toevallig bevatten; dat is de verantwoordelijkheid van de Verwerkingsverantwoordelijke.

4.3 [AANVULLEN: eventuele andere categorieën die deze client verwerkt.]

### Artikel 5. Instructies (art. 28 lid 3 sub a)

5.1 De Verwerker verwerkt persoonsgegevens uitsluitend op basis van schriftelijke instructies van de Verwerkingsverantwoordelijke, ook voor doorgifte naar een derde land, tenzij een Unierechtelijke of lidstaatrechtelijke bepaling hem tot verwerking verplicht. Dan meldt hij dat vooraf, tenzij de wet dat verbiedt.

5.2 Deze overeenkomst en het gebruik van de dienst door gebruikers van de Verwerkingsverantwoordelijke zijn de instructies. Aanvullende instructies gaan per e-mail naar [E-MAILADRES VERWERKER].

5.3 De Verwerker meldt de Verwerkingsverantwoordelijke direct als hij vindt dat een instructie in strijd is met de AVG of andere regels voor gegevensbescherming (art. 28 lid 3, laatste alinea).

### Artikel 6. Subverwerkers (art. 28 lid 2 en 4)

6.1 De Verwerkingsverantwoordelijke geeft de Verwerker algemene toestemming voor de subverwerkers in onderstaande tabel.

| Subverwerker | Doel | Locatie | Gegevens | Voorwaarde |
|---|---|---|---|---|
| Hetzner Online GmbH | hosting van server en back-ups | EU, [HETZNER LOCATIE] | alle gegevens in de dienst | verwerkersovereenkomst met Hetzner, [CONTROLEER DE HUIDIGE VOORWAARDEN OP DE SITE VAN HETZNER] |
| Anthropic | alleen als documentextractie is ingeschakeld: lezen van foto's en scans van facturen | mogelijk buiten de EU | het ingelezen document (pdf of afbeelding) | zie 6.3 |
| [E-MAIL- OF BACK-UPDIENST, indien van toepassing] | [DOEL] | [LOCATIE] | [GEGEVENS] | [VOORWAARDE] |

6.2 De dienst is standaard geleverd met documentextractie **uitgeschakeld** (`LEDGERLINE_EXTRACTION_PROVIDER=none`, zie `docs/EXTRACTION.md`). Zonder deze extractie verlaten geen documenten de server van de Verwerker. De Verwerker schakelt extractie alleen in na schriftelijke toestemming van de Verwerkingsverantwoordelijke.

6.3 Anthropic wordt alleen subverwerker zodra extractie is ingeschakeld. Voordat dat gebeurt controleert de Verwerker bij de aanbieder de actuele voorwaarden: de verwerkersovereenkomst, de bewaartermijn van verzonden gegevens, of die voor training worden gebruikt, en op welke grondslag gegevens buiten de EU worden verwerkt (bijvoorbeeld een adequaatheidsbesluit of standaardcontractbepalingen). Deze controle is **nog niet uitgevoerd** [DATUM EN RESULTAAT INVULLEN VOORDAT EXTRACTIE WORDT INGESCHAKELD]. Kan dat niet worden onderbouwd, dan blijft extractie uit.

6.4 Voegt de Verwerker een subverwerker toe of vervangt hij er een, dan meldt hij dat de Verwerkingsverantwoordelijke minimaal [30] dagen vooraf per e-mail. De Verwerkingsverantwoordelijke mag binnen [14] dagen schriftelijk en gemotiveerd bezwaar maken. Lossen Partijen het bezwaar niet op, dan mag de Verwerkingsverantwoordelijke de dienst beëindigen volgens artikel 12.

6.5 De Verwerker legt aan elke subverwerker dezelfde verplichtingen op als in deze overeenkomst en blijft tegenover de Verwerkingsverantwoordelijke verantwoordelijk voor diens naleving (art. 28 lid 4).

### Artikel 7. Vertrouwelijkheid (art. 28 lid 3 sub b)

7.1 De Verwerker zorgt dat personen die toegang hebben tot de persoonsgegevens gebonden zijn aan geheimhouding, contractueel of wettelijk. [NAMEN OF AANTAL PERSONEN MET TOEGANG: bijvoorbeeld alleen de exploitant.]

7.2 De Verwerker raadpleegt de administratie van de Verwerkingsverantwoordelijke alleen als dat nodig is voor ondersteuning of storingen en doet dat niet zonder aanleiding.

### Artikel 8. Beveiliging (art. 28 lid 3 sub c, art. 32 AVG)

8.1 De Verwerker treft passende technische en organisatorische maatregelen. De maatregelen die de software zelf biedt staan in `docs/SECURITY.md` (versie [COMMIT OF DATUM]) en omvatten onder meer:

- wachtwoorden met argon2id, minimaal 12 tekens; sessiecookie `Secure` en `SameSite=Lax`; CSRF-controle op elke schrijfactie; beperking van inlogpogingen (5 mislukte pogingen per e-mailadres in 15 minuten);
- scheiding van klanten: iedere klant is een eigen tenant, afgedwongen door PostgreSQL row-level security op elke tabel met boekhoudgegevens (ENABLE en FORCE); de applicatie draait onder een databaserol zonder superuser- of BYPASSRLS-rechten (zie `docs/adr/0001-multi-tenancy.md`);
- rollen per tenant (eigenaar, boekhouder, alleen-lezen accountant); exports van persoonsgegevens alleen voor de eigenaar;
- strikte Content-Security-Policy, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin`;
- journaalboekingen en auditlog zijn append-only, afgedwongen door databasetriggers;
- geen uitgaande netwerkverbindingen en geen externe bronnen tijdens gebruik, behalve de documentextractie als die is ingeschakeld;
- de container draait zonder rootrechten.

8.2 De Verwerker voert daarnaast uit: HTTPS-only, versleutelde schijf of volume op de server [BEVESTIGEN: ja/nee, schijfversleuteling is geen onderdeel van de software], dagelijkse back-up met kopie buiten de server [BEVESTIGEN: locatie en versleuteling], kwartaalcontrole van het herstel (zie `docs/BACKUP_RESTORE.md`), regelmatig bijwerken van software en images, geen publieke databasepoort, sterke willekeurige geheimen die niet in de code staan.

8.3 Bekende beperkingen die de Verwerkingsverantwoordelijke accepteert, zoals vermeld onder "What it does not do" in `docs/SECURITY.md`: [CONTROLEER BIJ ONDERTEKENING OF TWEEFACTORAUTHENTICATIE EN WACHTWOORDHERSTEL INMIDDELS ZIJN TOEGEVOEGD EN PAS DIT AAN], geen versleuteling van gegevens in rust door de software zelf, geen virusscan van uploads, geen ratelimiting buiten de inlogbeperking en de upload-API.

8.4 De Verwerker houdt de maatregelen actueel. Wijzigingen mogen het beveiligingsniveau niet verlagen.

### Artikel 9. Bijstand (art. 28 lid 3 sub e en f)

9.1 Verzoeken van betrokkenen (inzage, rectificatie, wissing, beperking, overdraagbaarheid, bezwaar) beantwoordt de Verwerkingsverantwoordelijke zelf. De Verwerker verwijst een verzoek dat hij rechtstreeks ontvangt door en beantwoordt het niet zelf.

9.2 De Verwerker helpt daarbij met wat de dienst biedt: per contact een export als JSON, de hele administratie als ZIP (beide alleen voor de eigenaar, onder Instellingen, Privacy en data) en het anonimiseren van een contact. Wissing is beperkt door de wettelijke bewaarplicht voor de administratie (7 jaar, 10 jaar voor onroerend goed); de dienst anonimiseert in plaats van te verwijderen (zie `docs/COMPLIANCE_NL.md`). De Verwerkingsverantwoordelijke beoordeelt of anonimiseren voldoet.

9.3 De Verwerker helpt de Verwerkingsverantwoordelijke, rekening houdend met de aard van de verwerking en de beschikbare gegevens, bij zijn verplichtingen over beveiliging, melding van datalekken, gegevensbeschermingseffectbeoordelingen en voorafgaande raadpleging (art. 32 tot en met 36 AVG). Redelijke tijd en kosten: [AFSPRAAK, bijvoorbeeld binnen de pilot zonder kosten].

### Artikel 10. Melding van datalekken (art. 28 lid 3 sub f, art. 33 AVG)

10.1 De Verwerker meldt een inbreuk op persoonsgegevens die de Verwerkingsverantwoordelijke raakt **zonder onredelijke vertraging, en streeft ernaar dat binnen 24 uur nadat hij ervan op de hoogte is** te doen, zodat de Verwerkingsverantwoordelijke zijn eigen meldplicht van 72 uur aan de Autoriteit Persoonsgegevens kan nakomen (art. 33 lid 1 AVG). De melding gaat per e-mail en telefoon naar [CONTACTPERSOON, E-MAIL, TELEFOON].

10.2 De melding bevat voor zover bekend: de aard van de inbreuk, de betrokken categorieën en een indicatie van het aantal betrokkenen en records, de waarschijnlijke gevolgen en de genomen of voorgestelde maatregelen. Ontbrekende informatie volgt zodra die er is.

10.3 De Verwerker meldt zelf niet bij de Autoriteit Persoonsgegevens of betrokkenen, tenzij de Verwerkingsverantwoordelijke dat vraagt of de wet het eist. De Verwerker houdt een logboek van inbreuken bij.

### Artikel 11. Audit (art. 28 lid 3 sub h)

11.1 De Verwerker geeft de Verwerkingsverantwoordelijke de informatie die nodig is om naleving van art. 28 aan te tonen, waaronder de actuele `docs/SECURITY.md`, `docs/BACKUP_RESTORE.md` en het laatste ingevulde acceptatieformulier (`docs/ACCEPTANCE_TEST.md`) en het resultaat van de laatste herstelcontrole.

11.2 De Verwerkingsverantwoordelijke of een door hem aangewezen auditor mag de naleving controleren, met [30] dagen vooraankondiging, maximaal [één] keer per jaar, tijdens kantooruren, onder geheimhouding en zonder de gegevens van andere klanten in te zien. Kosten van de audit zijn voor de Verwerkingsverantwoordelijke, tenzij de audit een wezenlijke tekortkoming aantoont. [CONTROLEER MET JURIST]

### Artikel 12. Einde: teruggave of verwijdering (art. 28 lid 3 sub g)

12.1 Na afloop van de dienst levert de Verwerker, naar keuze van de Verwerkingsverantwoordelijke, alle persoonsgegevens terug en verwijdert hij daarna de kopieën, of verwijdert hij ze. De Verwerkingsverantwoordelijke maakt zijn keuze binnen [30] dagen na afloop schriftelijk bekend; zonder keuze wordt teruggegeven en daarna verwijderd.

12.2 Teruggave gebeurt als export van de administratie (ZIP uit Instellingen, Privacy en data, aangevuld met een databasedump van die klant [BEVESTIGEN: de applicatie kan nog geen dump per tenant maken]) in een gangbaar formaat via een beveiligde route.

12.3 De Verwerker verwijdert de gegevens van de actieve omgeving binnen [30] dagen na bevestiging dat de export is ontvangen, en uit back-ups binnen de bewaartermijn van de back-ups ([30] dagen, zie `BACKUP_KEEP_DAYS` in `docs/BACKUP_RESTORE.md`). Hij bevestigt de verwijdering schriftelijk. [OPEN PUNT: de applicatie kan een tenant nog niet verwijderen; de huidige handmatige procedure staat in `docs/pilot/ONBOARDING_RUNBOOK.md`.]

12.4 Bewaarplicht: de Verwerkingsverantwoordelijke blijft zelf verantwoordelijk voor de fiscale bewaarplicht (in het algemeen 7 jaar) en bewaart daarvoor zijn eigen export. De Verwerker bewaart de administratie niet langer dan hierboven, tenzij de wet hem daartoe verplicht.

### Artikel 13. Aansprakelijkheid

13.1 Voor aansprakelijkheid geldt wat in de Pilotovereenkomst is afgesproken ([VERWIJZING NAAR ARTIKEL]). De wettelijke aansprakelijkheid van partijen jegens betrokkenen (art. 82 AVG) blijft onverlet. [CONTROLEER MET JURIST: de beperking in de pilot mag niet in strijd komen met art. 82 AVG en eventuele dwingend recht.]

### Artikel 14. Slotbepalingen

14.1 Bij strijd tussen deze overeenkomst en de Pilotovereenkomst gaat deze overeenkomst voor voor zover het de verwerking van persoonsgegevens betreft.

14.2 Op deze overeenkomst is Nederlands recht van toepassing. Geschillen gaan naar de bevoegde rechter in [PLAATS].

14.3 Wijzigingen zijn alleen geldig als ze schriftelijk zijn overeengekomen. Mocht een bepaling nietig zijn, dan blijft de rest gelden.

**Ondertekening**

| | Verwerkingsverantwoordelijke | Verwerker |
|---|---|---|
| Naam | [NAAM] | [NAAM] |
| Functie | [FUNCTIE] | [FUNCTIE] |
| Datum | | |
| Plaats | | |
| Handtekening | | |

## Bijlage 1. Technische en organisatorische maatregelen

Zie artikel 8 en `docs/SECURITY.md`. [VOEG DE ACTUELE VERSIE OF EEN UITTREKSEL TOE BIJ ONDERTEKENING.]

## Bijlage 2. Contactpersonen

| Rol | Verwerkingsverantwoordelijke | Verwerker |
|---|---|---|
| Privacycontact | [NAAM, E-MAIL] | [NAAM, E-MAIL] |
| Beveiligingsmeldingen, 24 uur bereikbaar | [NAAM, TELEFOON] | [NAAM, TELEFOON] |

## Bronnen

- Verordening (EU) 2016/679 (AVG), artikel 28: <https://eur-lex.europa.eu/eli/reg/2016/679/oj>. Artikel 28 lid 3 noemt de verplichte inhoud (onderwerp, duur, aard, doel, soort gegevens, categorieën betrokkenen, rechten en plichten, instructies, geheimhouding, beveiliging, subverwerkers, bijstand, verwijdering of teruggave, audit). Artikel 32 (beveiliging), 33 (melding datalek aan toezichthouder, 72 uur) en 82 (aansprakelijkheid) staan in dezelfde verordening.
- Niet gecontroleerd in dit sjabloon: de huidige voorwaarden en doorgifteregeling van Hetzner en Anthropic. Voor beide is het de taak van de Verwerker die te controleren en in artikel 6 vast te leggen.
