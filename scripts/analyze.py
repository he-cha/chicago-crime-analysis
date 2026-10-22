"""Validate public rollups, analyze them with SQLite, and render figures."""
from pathlib import Path
import calendar
import json
import sqlite3
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, PercentFormatter

ROOT = Path(__file__).resolve().parents[1]
BLUE, ORANGE, INK, MUTED = '#25636B', '#C37242', '#203438', '#607176'
SQL_TRENDS = '''
WITH moments AS (
 SELECT district, COUNT(*) AS years,
 AVG(year*1.0) AS xbar, AVG(incidents*1.0) AS ybar,
 SUM(year*year*1.0)-SUM(year*1.0)*SUM(year*1.0)/COUNT(*) AS sxx,
 SUM(year*incidents*1.0)-SUM(year*1.0)*SUM(incidents*1.0)/COUNT(*) AS sxy,
 SUM(incidents*incidents*1.0)-SUM(incidents*1.0)*SUM(incidents*1.0)/COUNT(*) AS syy
 FROM district_annual
 WHERE year BETWEEN 2010 AND 2024 AND district BETWEEN 1 AND 25
 GROUP BY district
)
SELECT district, years, ybar AS mean_incidents,
 sxy/NULLIF(sxx,0) AS slope,
 sxy*sxy/NULLIF(sxx*syy,0) AS r_squared,
 100.0*sxy/NULLIF(sxx*ybar,0) AS slope_pct_of_mean
FROM moments WHERE years=15 ORDER BY slope_pct_of_mean;
'''
SQL_ANNUAL = '''
SELECT year, incidents, arrests,
 100.0*arrests/NULLIF(incidents,0) AS arrest_pct,
 AVG(incidents) OVER (ORDER BY year ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS incidents_ma5,
 AVG(arrests) OVER (ORDER BY year ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS arrests_ma5
FROM annual WHERE year < 2025 ORDER BY year;
'''

def load():
    frames = {name:pd.read_csv(ROOT/'data'/f'{name}.csv') for name in ['annual','monthly','district_annual','category_annual']}
    # Source district codes include both '016' and '16'; normalize and combine.
    frames['district_annual'] = frames['district_annual'].groupby(['year','district'], dropna=False, as_index=False)[['incidents','arrests']].sum()
    for name, df in frames.items():
        assert df['year'].between(2001,2025).all(), name
        assert df['incidents'].gt(0).all() and df['arrests'].between(0,df['incidents']).all(), name
        keys = {'annual':['year'],'monthly':['year','month'],'district_annual':['year','district'],'category_annual':['year','primary_type']}[name]
        assert not df.duplicated(keys).any(), name
    baseline=frames['annual'].set_index('year')[['incidents','arrests']].sort_index()
    for name in ['monthly','district_annual','category_annual']:
        pd.testing.assert_frame_equal(frames[name].groupby('year')[['incidents','arrests']].sum().sort_index(),baseline,check_dtype=False)
    check=json.loads((ROOT/'data/source_check.json').read_text())
    assert int(check['records']) == int(baseline['incidents'].sum())
    assert len(frames['monthly'])==25*12-1, 'Expected all months through November 2025'
    assert frames['monthly'].query('year < 2025').groupby('year').size().eq(12).all()
    db=sqlite3.connect(':memory:')
    for name, df in frames.items():
        df.to_sql(name, db,index=False)
    return frames,db

def style():
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.titleweight':'bold','axes.titlesize':15,'axes.labelcolor':INK,'text.color':INK,'xtick.color':MUTED,'ytick.color':MUTED,'axes.spines.top':False,'axes.spines.right':False,'axes.spines.left':False,'axes.spines.bottom':False,'figure.facecolor':'#FAFBF9','axes.facecolor':'#FAFBF9','savefig.facecolor':'#FAFBF9','axes.axisbelow':True})

def save(fig,name,caption):
    fig.text(.065,.018,caption,fontsize=9,color=MUTED)
    fig.savefig(ROOT/'figures'/f'{name}.png',dpi=180,bbox_inches='tight')
    plt.close(fig)

def run():
    (ROOT/'figures').mkdir(exist_ok=True)
    frames,db=load();style()
    annual=pd.read_sql_query(SQL_ANNUAL,db)
    trends=pd.read_sql_query(SQL_TRENDS,db)
    # Verify SQL regression calculations independently with NumPy.
    for row in trends.itertuples():
        d=frames['district_annual'].query('district == @row.district and 2010 <= year <= 2024')
        assert np.isclose(row.slope,np.polyfit(d.year,d.incidents,1)[0],rtol=1e-8)
    for query, name in [(SQL_ANNUAL,'annual_analysis'),(SQL_TRENDS,'district_trends')]:
        pd.read_sql_query(query,db).to_csv(ROOT/'data'/f'{name}.csv',index=False)
    fig,(ax,rate)=plt.subplots(2,1,figsize=(11,8),sharex=True,gridspec_kw={'height_ratios':[1.6,1]})
    fig.subplots_adjust(hspace=.3,bottom=.12,top=.89)
    fig.suptitle('Fewer recorded incidents, a smaller share linked to arrests',x=.065,ha='left',fontsize=18,fontweight='bold')
    for col,ma,label,color in [('incidents','incidents_ma5','Reported incident records',BLUE),('arrests','arrests_ma5','Records with arrest = true',ORANGE)]:
        ax.plot(annual.year,annual[col],color=color,alpha=.25,lw=1.5)
        ax.plot(annual.year,annual[ma],color=color,lw=2.6,label=label+' · 5-year average')
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x,p:f'{x/1000:.0f}k'))
    ax.set_ylabel('Records');ax.grid(axis='y',alpha=.15);ax.legend(frameon=False,loc='upper right',fontsize=9)
    rate.plot(annual.year,annual.arrest_pct,color=ORANGE,lw=2.4)
    rate.fill_between(annual.year,annual.arrest_pct,alpha=.09,color=ORANGE)
    rate.set_ylim(0,40);rate.yaxis.set_major_formatter(PercentFormatter(100));rate.set_ylabel('Share with arrest');rate.set_xlabel('Incident year');rate.grid(axis='y',alpha=.15)
    save(fig,'01_annual_trends','Source: Chicago Data Portal. Full years 2001–2024. Arrest is a recorded attribute, not a conviction. Moving windows expand for the first four years.')
    # Seasonal intensity: daily records / daily annual average, to account for month lengths and annual scale.
    monthly=frames['monthly'].query('year < 2025').copy()
    monthly['days']=[calendar.monthrange(int(y),int(m))[1] for y,m in zip(monthly.year,monthly.month)]
    monthly['daily']=monthly.incidents/monthly.days
    yearly=monthly.groupby('year').apply(lambda d:d.incidents.sum()/d.days.sum(),include_groups=False)
    monthly['relative']=monthly.daily/monthly.year.map(yearly)
    heat=monthly.pivot(index='year',columns='month',values='relative')
    fig,ax=plt.subplots(figsize=(11,9));fig.subplots_adjust(bottom=.1,top=.89,left=.1)
    image=ax.imshow(heat.values,cmap='BrBG_r',vmin=.7,vmax=1.3,aspect='auto')
    ax.set_xticks(range(12),calendar.month_abbr[1:]);ax.set_yticks(range(len(heat)),heat.index)
    ax.set_title('Seasonality persists beyond changes in annual totals',loc='left',pad=22,fontsize=17)
    ax.set_ylabel('Incident year');ax.tick_params(length=0)
    cb=fig.colorbar(image,ax=ax,pad=.025,shrink=.7);cb.set_label('Daily records / annual daily average\n1.0 = that year’s average')
    save(fig,'02_seasonality','Source: Chicago Data Portal. 2001–2024. Month lengths and leap years are accounted for. Relative intensity describes reported records.')
    cat=frames['category_annual'].query('year == 2024').nlargest(10,'incidents').sort_values('incidents')
    fig,(ax,ar)=plt.subplots(1,2,figsize=(12,6),gridspec_kw={'width_ratios':[2.2,1]},sharey=True)
    fig.subplots_adjust(left=.3,wspace=.08,bottom=.16,top=.82)
    fig.suptitle('Crime categories differ in volume and recorded arrest share',x=.065,ha='left',fontsize=17,fontweight='bold')
    ax.barh(cat.primary_type,cat.incidents,color=BLUE,height=.62);ax.set_title('Most common categories · 2024',loc='left',fontsize=12)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x,p:f'{x/1000:.0f}k'));ax.set_xlabel('Reported incident records');ax.grid(axis='x',alpha=.15)
    shares=100*cat.arrests/cat.incidents
    ar.scatter(shares,cat.primary_type,color=ORANGE,s=50);ar.set_xlim(0,100);ar.set_xticks([0,50,100],['0%','50%','100%']);ar.set_xlabel('Share with arrest');ar.set_title('Same categories',fontsize=12);ar.tick_params(axis='y',left=False,labelleft=False);ar.grid(axis='x',alpha=.15)
    for y,value in zip(cat.primary_type,shares):ar.annotate(f'{value:.1f}%',(value,y),xytext=(7,0),textcoords='offset points',va='center',fontsize=9)
    save(fig,'03_categories','Source: Chicago Data Portal. Ten largest categories in 2024. Category labels are source classifications; arrest shares do not measure severity.')
    city=annual.query('2010 <= year <= 2024');city_slope=float(np.polyfit(city.year,city.incidents,1)[0]);city_scaled=city_slope/city.incidents.mean()*100
    fig,ax=plt.subplots(figsize=(11,8));fig.subplots_adjust(left=.15,bottom=.14,top=.85)
    colors=[ORANGE if x>0 else BLUE for x in trends.slope_pct_of_mean]
    ax.barh([f'District {int(x):02}' for x in trends.district],trends.slope_pct_of_mean,color=colors,height=.66)
    ax.axvline(0,color=INK,lw=.8);ax.axvline(city_scaled,color=INK,ls='--',lw=1.3,label=f'Citywide: {city_scaled:+.2f}% of mean / year')
    ax.set_title('Citywide trends can hide different district patterns',loc='left',fontsize=17,pad=30)
    ax.set_xlabel('Linear slope as % of each district’s mean annual records');ax.grid(axis='x',alpha=.15);ax.legend(frameon=False,loc='lower right',fontsize=9)
    save(fig,'04_district_trends','Source: Chicago Data Portal. 2010–2024; codes 01–25 with all 15 years. Descriptive linear fit, not a growth forecast or population-adjusted risk.')
    first=annual.iloc[0];last=annual.iloc[-1]
    summary={'total_records_in_scope':int(frames['annual'].incidents.sum()),'distinct_cases':int(json.loads((ROOT/'data/source_check.json').read_text())['distinct_cases']), 'first_year':int(first.year),'last_full_year':int(last.year),'incident_change_pct':float((last.incidents/first.incidents-1)*100),'first_arrest_pct':float(first.arrest_pct),'last_arrest_pct':float(last.arrest_pct),'city_slope_2010_2024':city_slope,'districts_positive_slope':int((trends.slope>0).sum()),'districts_negative_slope':int((trends.slope<0).sum()),'districts_complete':len(trends),'summer_relative_intensity':float(monthly.query('month in [6,7,8]').relative.mean()),'winter_relative_intensity':float(monthly.query('month in [12,1,2]').relative.mean())}
    (ROOT/'data/summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
    print('All reconciliation and regression checks passed.')
    return frames,annual,trends,summary

if __name__=='__main__':run()
