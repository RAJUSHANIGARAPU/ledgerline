"""Primary sources behind the VAT return mapping, rounding, deadline and KOR rules."""

_BD = "https://www.belastingdienst.nl/wps/wcm/connect/"
_OLD_TOELICHTING = "https://download.belastingdienst.nl/belastingdienst/docs/toelichting_aangifte_omzetbelasting_ob1411t12pl.pdf"
# Where each rubriek's meaning was checked (2026-11-08). "current" = a current belastingdienst.nl
# page; "2012" = only the 2012 explanation of the form was found, so the rubriek keeps a verify mark.
SOURCES: dict[str, tuple[str, str]] = {
    "1a": ("current", _BD + "nl/btw/content/ik-moet-btw-aangifte-doen-hoe-vul-ik-die-in"),
    "1b": ("2012", _OLD_TOELICHTING),
    "1c": ("2012", _OLD_TOELICHTING),
    "1d": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/btw_aftrekken/btw_en_de_auto/"
           "privegebruik_auto_van_de_zaak/privegebruik_auto_van_de_zaak"),
    "1e": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/btw_berekenen_aan_uw_klanten/"
           "waarover_btw_berekenen/verleggingsregeling/hoe_werkt_btw_verleggen"),
    "2a": ("2012", _OLD_TOELICHTING),
    "3a": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/zakendoen_met_het_buitenland/"
           "aangifte_doen_als_u_zakendoet_buiten_de_eu/aangifte_en_als_u_goederen_uitvoert_naar_niet_eu_landen"),
    "3b": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/btw_berekenen_aan_uw_klanten/"
           "waarover_btw_berekenen/verleggingsregeling/hoe_werkt_btw_verleggen"),
    "3c": ("2012", _OLD_TOELICHTING),
    "4a": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/zakendoen_met_het_buitenland/"
           "aangifte_doen_als_u_zakendoet_buiten_de_eu/"
           "aangifte_doen_als_u_diensten_afneemt_van_leveranciers_uit_niet_eu_landen"),
    "4b": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/zakendoen_met_het_buitenland/"
           "goederen_en_diensten_afnemen_uit_andere_eu_landen/aangifte_doen/"
           "aangifte-doen-van-goederen-en-diensten-uit-andere-eu-landen"),
    "5b": ("current", _BD + "nl/btw/content/ik-moet-btw-aangifte-doen-hoe-vul-ik-die-in"),
    "rounding": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/administratie_bijhouden/"
                 "facturen_maken/btw-bedrag_afronden"),
    "deadline": ("current", _BD + "nl/btw/content/uiterste-aangifte-en-betaaldatums"),
    "kor": ("current", _BD + "bldcontentnl/belastingdienst/zakelijk/btw/hoe_werkt_de_btw/"
            "kleineondernemersregeling/kleineondernemersregeling"),
}
