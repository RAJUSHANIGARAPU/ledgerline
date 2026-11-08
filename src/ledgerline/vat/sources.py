"""Primary sources behind the VAT return mapping, rounding, deadline and KOR rules."""

_BD = "https://www.belastingdienst.nl/wps/wcm/connect/"
# Where each rule was checked (2026-11-08). "current" = a current belastingdienst.nl page; any
# other status puts a verify mark on the rubriek in the app.

# Belastingdienst page explaining every rubriek (updated 21-09-2026; written for foundations and
# associations, the rubrieken are the same form).
_RUBRIEKEN_PAGE = "https://stichtingenvereniging.belastingdienst.nl/aangifte/aangifte-omzetbelasting/invullen-rubrieken/"

SOURCES: dict[str, tuple[str, str]] = {
    "1a": ("current", _BD + "nl/btw/content/ik-moet-btw-aangifte-doen-hoe-vul-ik-die-in"),
    "1b": ("current", _RUBRIEKEN_PAGE),
    "1c": ("current", _RUBRIEKEN_PAGE),
    "1d": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/btw_aftrekken/btw_en_de_auto/"
           "privegebruik_auto_van_de_zaak/privegebruik_auto_van_de_zaak"),
    "1e": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/btw_berekenen_aan_uw_klanten/"
           "waarover_btw_berekenen/verleggingsregeling/hoe_werkt_btw_verleggen"),
    "2a": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/btw_aftrekken/welke_btw_is_aftrekbaar/verlegde_btw_aftrekken"),
    "3a": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/zakendoen_met_het_buitenland/"
           "aangifte_doen_als_u_zakendoet_buiten_de_eu/aangifte_en_als_u_goederen_uitvoert_naar_niet_eu_landen"),
    "3b": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/btw_berekenen_aan_uw_klanten/"
           "waarover_btw_berekenen/verleggingsregeling/hoe_werkt_btw_verleggen"),
    "3c": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/zakendoen_met_het_buitenland/afstandsverkopen-zoals-e-commerce-en-diensten-voor-particulieren-in-andere-eu-landen/afstandsverkopen-zoals-e-commerce-binnen-de-eu/"),
    "4a": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/zakendoen_met_het_buitenland/"
           "aangifte_doen_als_u_zakendoet_buiten_de_eu/"
           "aangifte_doen_als_u_diensten_afneemt_van_leveranciers_uit_niet_eu_landen"),
    "4b": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/zakendoen_met_het_buitenland/"
           "goederen_en_diensten_afnemen_uit_andere_eu_landen/aangifte_doen/"
           "aangifte-doen-van-goederen-en-diensten-uit-andere-eu-landen"),
    "5b": ("current", _BD + "nl/btw/content/ik-moet-btw-aangifte-doen-hoe-vul-ik-die-in"),
    "rounding": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/administratie_bijhouden/"
                 "facturen_maken/btw-bedrag_afronden"),
    "rounding_all": ("current", _RUBRIEKEN_PAGE),
    "deadline": ("current", _BD + "nl/btw/content/uiterste-aangifte-en-betaaldatums"),
    "kor": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/hoe_werkt_de_btw/"
            "kleineondernemersregeling/kleineondernemersregeling"),
}
