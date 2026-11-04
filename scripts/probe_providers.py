"""Bounded public, credential-free probes. Stores metadata, never article bodies.
Run from project root: ../.venv/Scripts/python.exe scripts/probe_providers.py
No automatic retries, no challenge solving, no cookies, no paid endpoints.
"""
import argparse
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET
import httpx

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 2_000_000
ALLOWED = {'brapi.dev','api.gdeltproject.org','api.bcb.gov.br','dados.cvm.gov.br','g1.globo.com','rss.uol.com.br','www.cnnbrasil.com.br','cnnbrasil.com.br','news.yahoo.com','www.yahoo.com','finance.yahoo.com','www.nytimes.com','rss.nytimes.com','www.foxnews.com','moxie.foxnews.com','www.infomoney.com.br'}

class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []
    def handle_starttag(self, tag, attrs):
        d = dict(attrs); href = d.get('href','')
        if tag in ('link','a') and href and ('rss' in href.lower() or 'feed' in href.lower() or 'xml' in d.get('type','')):
            self.links.append(href)

def fetch(client, label, url, method='GET'):
    record = {'id':label,'requested_url':url,'collected_at':datetime.now(timezone.utc).isoformat(),'method':method}
    content = b''
    try:
        for step in range(4):
            if urlsplit(url).scheme != 'https' or urlsplit(url).hostname not in ALLOWED:
                record['outcome']='redirect_not_allowlisted'; break
            with client.stream(method,url) as response:
                record.update(status=response.status_code,final_url=str(response.url),headers={k:v for k,v in response.headers.items() if k in ('content-type','content-length','etag','last-modified','retry-after')})
                if response.is_redirect:
                    url=urljoin(url,response.headers['location']); continue
                if response.status_code >= 400:
                    record['outcome']='http_error'; break
                for chunk in response.iter_bytes():
                    content += chunk
                    if len(content)>LIMIT:
                        record['outcome']='size_limit'; content=b''; break
                record.setdefault('outcome','success'); break
        record['bytes_read']=len(content)
        if content and record['outcome']=='success':
            try:
                data=json.loads(content)
                record['json_type']=type(data).__name__
                if isinstance(data,dict):
                    record['keys']=list(data)
                    for key in ('pagination','facets','requestedAt','error','message'):
                        if key in data: record[key]=data[key]
                    for key in ('results','articles'):
                        if isinstance(data.get(key),list):
                            rows=data[key]; record['row_count']=len(rows)
                            record['sample']= [{k:v for k,v in r.items() if k not in ('description','content','summary','historicalDataPrice','dividendsData')} for r in rows[:3]]
                            if key=='results' and rows and 'historicalDataPrice' in rows[0]:
                                record['history_count']=len(rows[0]['historicalDataPrice']); record['history_sample']=rows[0]['historicalDataPrice'][:2]
                elif isinstance(data,list): record['row_count']=len(data); record['sample']=data[:3]
            except (ValueError,TypeError):
                try:
                    root=ET.fromstring(content)
                    items=root.findall('.//item'); record.update(xml_root=root.tag,item_count=len(items))
                    record['sample']=[{k:item.findtext(k) for k in ('link','pubDate')} for item in items[:3]]
                except ET.ParseError as exc:
                    record['xml_parse_error']=str(exc)
                    parser=Links(); parser.feed(content.decode('utf-8',errors='replace'))
                    record['discovered_feed_links']=list(dict.fromkeys(urljoin(url,x) for x in parser.links))[:30]
    except Exception as exc:
        record.update(outcome='transport_error',error_type=type(exc).__name__,error=str(exc)[:180])
    return record

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--phase',choices=['core','discovery','feeds','bcb_bounded'],default='core'); ap.add_argument('--source'); args=ap.parse_args()
    now=datetime.now(timezone.utc); end=now-timedelta(hours=1); start=end-timedelta(days=1)
    jobs=[]
    if args.phase=='core':
        jobs=[('brapi_catalog_page1','https://brapi.dev/api/v2/tickers?type=stock&limit=5&page=1&sortBy=symbol&sortOrder=asc'),('brapi_catalog_page2','https://brapi.dev/api/v2/tickers?type=stock&limit=5&page=2&sortBy=symbol&sortOrder=asc'),('brapi_units','https://brapi.dev/api/v2/tickers?type=stock&subType=unit&limit=5&sortBy=volume&sortOrder=asc')]
        jobs += [(f'brapi_quote_{ticker}',f'https://brapi.dev/api/quote/{ticker}?range=3mo&interval=1d') for ticker in ('PETR4','VALE3')]
        jobs += [('gdelt',str(httpx.URL('https://api.gdeltproject.org/api/v2/doc/doc',params={'query':'domainis:g1.globo.com (economy OR oil)','mode':'artlist','format':'json','maxrecords':'10','startdatetime':start.strftime('%Y%m%d%H%M%S'),'enddatetime':end.strftime('%Y%m%d%H%M%S')})))]
        jobs += [(f'bcb_{s}',f'https://api.bcb.gov.br/dados/serie/bcdata.sgs.{s}/dados/ultimos/3?formato=json') for s in (1178,432)]
        jobs += [('cvm_catalog','https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/')]
    elif args.phase=='bcb_bounded':
        jobs=[(f'bcb_{s}_bounded',str(httpx.URL(f'https://api.bcb.gov.br/dados/serie/bcdata.sgs.{s}/dados',params={'formato':'json','dataInicial':(now-timedelta(days=7)).strftime('%d/%m/%Y'),'dataFinal':now.strftime('%d/%m/%Y')}))) for s in (1178,432)]
    elif args.phase=='discovery':
        jobs=[('br_g1','https://g1.globo.com/economia/'),('br_uol','https://rss.uol.com.br/'),('br_cnnbr','https://www.cnnbrasil.com.br/'),('us_yahoo','https://news.yahoo.com/'),('us_nyt','https://www.nytimes.com/rss'),('us_fox','https://www.foxnews.com/story/foxnews-com-rss-feeds'),('fin_infomoney','https://www.infomoney.com.br/'),('fin_yahoo','https://finance.yahoo.com/')]
    else:
        config=json.loads((ROOT/'config/verified-feeds.json').read_text(encoding='utf-8'))
        jobs=[(s['id'],u) for s in config['sources'] for u in s.get('rss_urls',[])]
    if args.source: jobs=[job for job in jobs if job[0]==args.source]
    records=[]
    with httpx.Client(timeout=25,follow_redirects=False,headers={'User-Agent':'RadarB3-ProviderProbe/0.1 (personal research; metadata only)'}) as client:
        for label,url in jobs:
            r=fetch(client,label,url); records.append(r); print(label,r.get('status'),r['outcome'],flush=True)
            time.sleep(1)
        if args.phase=='core':
            unit=next((r['sample'][0]['symbol'] for r in records if r['id']=='brapi_units' and r.get('sample')),None)
            if unit: records.append(fetch(client,'brapi_quote_unit','https://brapi.dev/api/quote/'+unit+'?range=3mo&interval=1d'))
            records.append(fetch(client,'cvm_zip_head','https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_2025.zip','HEAD'))
    out=ROOT/'data/probes'; out.mkdir(parents=True,exist_ok=True)
    path=out/(args.phase+'-'+now.strftime('%Y%m%dT%H%M%SZ')+'.json'); path.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8'); print(path)

if __name__=='__main__': main()
