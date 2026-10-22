"""Fetch compact, record-count aggregates from Chicago's public Socrata API."""
import csv
import hashlib
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
ENDPOINT = 'https://data.cityofchicago.org/resource/ijzp-q8t2.json'
WHERE = "date >= '2001-01-01T00:00:00' AND date < '2025-11-24T00:00:00'"
# Keeping a fine-grained rollup permits subsequent SQL exploration locally,
# without downloading millions of individual incident/victim records.
QUERIES = {
 'annual': {'$select': 'year, count(*) AS incidents, sum(case(arrest = true, 1, true, 0)) AS arrests', '$group':'year', '$order':'year'},
 'monthly': {'$select': 'year, date_extract_m(date) AS month, count(*) AS incidents, sum(case(arrest = true, 1, true, 0)) AS arrests', '$group':'year, month', '$order':'year, month'},
 'district_annual': {'$select': 'year, district, count(*) AS incidents, sum(case(arrest = true, 1, true, 0)) AS arrests', '$group':'year, district', '$order':'year, district'},
 'category_annual': {'$select': 'year, primary_type, count(*) AS incidents, sum(case(arrest = true, 1, true, 0)) AS arrests', '$group':'year, primary_type', '$order':'year, primary_type'},
 'source_check': {'$select':'count(*) AS records, count(distinct case_number) AS distinct_cases, min(date) AS earliest, max(date) AS latest'},
}

def fetch(params):
    url = ENDPOINT + '?' + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return json.load(response)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)

def main():
    DATA.mkdir(exist_ok=True)
    manifest = {'endpoint':ENDPOINT, 'date_filter':WHERE, 'started_utc':datetime.now(timezone.utc).isoformat(), 'queries':{}}
    for name, query in QUERIES.items():
        params = {'$where': WHERE, '$limit': 50000, **query}
        rows = fetch(params)
        if not rows or len(rows) >= 50000:
            raise ValueError(f'Unexpected size for {name}: {len(rows)}')
        if name == 'source_check':
            path = DATA / f'{name}.json'
            path.write_text(json.dumps(rows[0],indent=2)+'\n')
        else:
            path = DATA / f'{name}.csv'
            fields = list(dict.fromkeys(k for row in rows for k in row))
            with path.open('w',newline='') as stream:
                writer = csv.DictWriter(stream,fieldnames=fields)
                writer.writeheader(); writer.writerows(rows)
        manifest['queries'][name] = {'params':params,'returned_rows':len(rows),'retrieved_utc':datetime.now(timezone.utc).isoformat(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        print(name, len(rows), flush=True)
    manifest['completed_utc'] = datetime.now(timezone.utc).isoformat()
    (DATA/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')

if __name__ == '__main__':
    main()
