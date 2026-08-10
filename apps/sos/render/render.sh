#!/bin/sh
set -eu

INPUT_JSON="${INPUT_JSON:-/data/data.json}"
OUTPUT_HTML="${OUTPUT_HTML:-/work/index.html}"
UNAVAILABLE_HTML="${UNAVAILABLE_HTML:-/renderer/unavailable.html}"

mkdir -p "$(dirname "$OUTPUT_HTML")"

escape_html() {
  if [ "$#" -eq 0 ]; then
    printf ""
    return
  fi

  printf '%s' "$1" | sed \
    -e 's/&/\&amp;/g' \
    -e 's/</\&lt;/g' \
    -e 's/>/\&gt;/g' \
    -e 's/"/\&quot;/g' \
    -e "s/'/\&#39;/g"
}

render_unavailable() {
  cp "$UNAVAILABLE_HTML" "$OUTPUT_HTML"
}

if [ ! -s "$INPUT_JSON" ]; then
  render_unavailable
  exit 0
fi

if ! jq -e . "$INPUT_JSON" >/dev/null 2>&1; then
  render_unavailable
  exit 0
fi

if ! jq -e '
  (.updatedAt | type == "string") and
  (.address.street | type == "string") and
  (.address.city | type == "string") and
  (.address.postalCode | type == "string") and
  (.address.country | type == "string") and
  (.address.googleMapsQuery | type == "string") and
  (.contacts | type == "array") and
  (.contacts | length >= 1) and
  (.contacts[0].name | type == "string") and
  (.contacts[0].relationship | type == "string") and
  (.contacts[0].phone | type == "string") and
  (.householdMembers | type == "array") and
  (.householdMembers | length >= 1) and
  (.householdMembers[0].displayName | type == "string")
' "$INPUT_JSON" >/dev/null 2>&1; then
  render_unavailable
  exit 0
fi

address_line_1="$(jq -r '.address.street' "$INPUT_JSON")"
address_line_2="$(jq -r '.address.postalCode + " " + .address.city + ", " + .address.country' "$INPUT_JSON")"
maps_query="$(jq -r '.address.googleMapsQuery' "$INPUT_JSON")"
updated_at="$(jq -r '.updatedAt' "$INPUT_JSON")"

address_line_1_esc="$(escape_html "$address_line_1")"
address_line_2_esc="$(escape_html "$address_line_2")"
updated_at_esc="$(escape_html "$updated_at")"
maps_url="https://www.google.com/maps/search/?api=1&query=$(printf '%s' "$maps_query" | jq -sRr @uri)"

tmp_contacts_file="$(mktemp)"
tmp_members_file="$(mktemp)"
cleanup() {
  rm -f "$tmp_contacts_file" "$tmp_members_file"
}
trap cleanup EXIT

jq -r '.contacts[] | @base64' "$INPUT_JSON" | while IFS= read -r row; do
  [ -z "$row" ] && continue
  name="$(printf '%s' "$row" | base64 -d | jq -r '.name')"
  relationship="$(printf '%s' "$row" | base64 -d | jq -r '.relationship')"
  phone="$(printf '%s' "$row" | base64 -d | jq -r '.phone')"

  name_esc="$(escape_html "$name")"
  relationship_esc="$(escape_html "$relationship")"
  phone_esc="$(escape_html "$phone")"
  phone_href="$(printf '%s' "$phone" | tr -cd '0-9+')"

  cat >>"$tmp_contacts_file" <<EOF
    <article class="card">
      <h3>${name_esc}</h3>
      <p><strong>DE:</strong> Beziehung: ${relationship_esc}</p>
      <p><strong>EN:</strong> Relationship: ${relationship_esc}</p>
      <p><a href="tel:${phone_href}">${phone_esc}</a></p>
    </article>
EOF
done

jq -r '.householdMembers[] | @base64' "$INPUT_JSON" | while IFS= read -r row; do
  [ -z "$row" ] && continue
  name="$(printf '%s' "$row" | base64 -d | jq -r '.displayName')"
  allergies_de="$(printf '%s' "$row" | base64 -d | jq -r '.allergies.de // "nicht bekannt"')"
  allergies_en="$(printf '%s' "$row" | base64 -d | jq -r '.allergies.en // "unknown"')"
  blood_type="$(printf '%s' "$row" | base64 -d | jq -r '.bloodType // "unknown"')"
  physician_name="$(printf '%s' "$row" | base64 -d | jq -r '.physician.name // "unknown"')"
  physician_phone="$(printf '%s' "$row" | base64 -d | jq -r '.physician.phone // ""')"

  name_esc="$(escape_html "$name")"
  allergies_de_esc="$(escape_html "$allergies_de")"
  allergies_en_esc="$(escape_html "$allergies_en")"
  blood_type_esc="$(escape_html "$blood_type")"
  physician_name_esc="$(escape_html "$physician_name")"
  physician_phone_esc="$(escape_html "$physician_phone")"
  physician_phone_href="$(printf '%s' "$physician_phone" | tr -cd '0-9+')"

  if [ -n "$physician_phone_href" ]; then
    physician_line="<a href=\"tel:${physician_phone_href}\">${physician_phone_esc}</a>"
  else
    physician_line="${physician_phone_esc}"
  fi

  cat >>"$tmp_members_file" <<EOF
    <article class="card">
      <h3>${name_esc}</h3>
      <p><strong>DE:</strong> Allergien: ${allergies_de_esc}</p>
      <p><strong>EN:</strong> Allergies: ${allergies_en_esc}</p>
      <p><strong>DE:</strong> Blutgruppe: ${blood_type_esc}</p>
      <p><strong>EN:</strong> Blood type: ${blood_type_esc}</p>
      <p><strong>DE:</strong> Hausarzt: ${physician_name_esc}</p>
      <p><strong>EN:</strong> Primary physician: ${physician_name_esc}</p>
      <p>${physician_line}</p>
    </article>
EOF
done

cat >"$OUTPUT_HTML" <<EOF
<!doctype html>
<html lang="de">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>SOS Reference</title>
    <style>
      :root {
        color-scheme: light;
        --bg: #f4f6f8;
        --surface: #ffffff;
        --text: #1a1d21;
        --muted: #4f5965;
        --danger: #b00020;
        --danger-soft: #ffe9ec;
        --border: #d5dbe3;
        --link: #0a66c2;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        font-family: "Source Sans 3", "Segoe UI", sans-serif;
        background: linear-gradient(180deg, #eef3f8, var(--bg));
        color: var(--text);
      }
      .wrap {
        max-width: 960px;
        margin: 0 auto;
        padding: 1rem;
      }
      h1, h2, h3 { margin: 0 0 0.5rem; }
      .banner {
        background: var(--danger-soft);
        border: 2px solid var(--danger);
        border-radius: 12px;
        padding: 1rem;
      }
      .actions {
        display: flex;
        flex-wrap: wrap;
        gap: 0.75rem;
        margin-top: 0.75rem;
      }
      .btn {
        display: inline-block;
        padding: 0.6rem 0.9rem;
        border-radius: 8px;
        text-decoration: none;
        font-weight: 700;
        background: var(--danger);
        color: #fff;
      }
      .section {
        margin-top: 1rem;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 1rem;
      }
      .cards {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 0.75rem;
      }
      .card {
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 0.75rem;
        background: #fff;
      }
      p { margin: 0.3rem 0; }
      .muted { color: var(--muted); }
      a { color: var(--link); }
    </style>
  </head>
  <body>
    <main class="wrap">
      <section class="banner">
        <h1>SOS / Notfall</h1>
        <p><strong>DE:</strong> Bei lebensbedrohlicher Situation zuerst Notruf waehlen.</p>
        <p><strong>EN:</strong> In life-threatening emergencies, call emergency services first.</p>
        <div class="actions">
          <a class="btn" href="tel:112">112 Feuerwehr/Rettung/Notarzt</a>
          <a class="btn" href="tel:110">110 Polizei</a>
        </div>
      </section>

      <section class="section">
        <h2>Adresse / Address</h2>
        <p>${address_line_1_esc}</p>
        <p>${address_line_2_esc}</p>
        <p><a href="${maps_url}" rel="noopener noreferrer">Google Maps directions</a></p>
      </section>

      <section class="section">
        <h2>Hauptkontakte / Primary Contacts</h2>
        <div class="cards">
$(cat "$tmp_contacts_file")
        </div>
      </section>

      <section class="section">
        <h2>Haushalt Medizin / Household Medical</h2>
        <div class="cards">
$(cat "$tmp_members_file")
        </div>
      </section>

      <section class="section">
        <p class="muted"><strong>Last updated:</strong> ${updated_at_esc}</p>
      </section>
    </main>
  </body>
</html>
EOF
