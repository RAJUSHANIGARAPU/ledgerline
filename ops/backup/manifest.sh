# shellcheck shell=sh
# Sourced by backup.sh and scripts/restore-drill.sh. POSIX sh.
# Manifest: key=value header lines, then one "<table><TAB><rows>" line per table.

write_manifest() { # counts_file dump_file alembic_revision out_file
  {
    echo "# ledgerline backup manifest v1"
    echo "created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "alembic_revision=$3"
    echo "dump_sha256=$(sha256_of "$2")"
    cat "$1"
  } > "$4"
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

manifest_value() { # manifest key
  sed -n "s/^$2=//p" "$1" | head -n 1
}

manifest_counts() { # manifest
  awk -F'\t' 'NF == 2 && $1 !~ /=/ { print $1 "\t" $2 }' "$1" | sort
}

# Prints one line per table that differs, nothing when they all match. Exit 1 on any difference.
compare_counts() { # manifest actual_counts_file
  manifest_counts "$1" > "$2.expected"
  sort "$2" > "$2.actual"
  awk -F'\t' '
    NR == FNR { want[$1] = $2; next }
    { got[$1] = $2 }
    END {
      bad = 0
      for (t in want) if (!(t in got)) { print t ": missing after restore (expected " want[t] ")"; bad = 1 }
        else if (got[t] != want[t]) { print t ": expected " want[t] ", restored " got[t]; bad = 1 }
      exit bad
    }' "$2.expected" "$2.actual"
}
