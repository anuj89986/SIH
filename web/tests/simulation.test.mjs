import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {simulate,defaults,weather,forecast,validateConfig,csvFor,dispatch} from '../dist/simulation.js';
import {STATION} from '../dist/data/maitri-2023.js';
const close=(a,b)=>assert.ok(Math.abs(a-b)<1e-7,`${a} differs from ${b}`);
function physicalChecks(run){const c=run.config;for(const rr of run.runs){let energy=c.battery*c.initialSoc/100,fuel=c.fuel;for(const r of rr.rows){
 close(r.pv+r.wind+r.generatorPower+r.batteryPower,r.load-r.unserved+r.curtailed);
 close(r.energy,energy+Math.max(0,-r.batteryPower)*.95-Math.max(0,r.batteryPower)/.95);
 close(r.fuelRemaining,fuel-r.fuelUsed);
 close(r.unserved,r.criticalUnserved+r.essentialUnserved+r.domesticUnserved);
 assert.ok(r.energy>=c.battery*.15-1e-8&&r.energy<=c.battery*.95+1e-8);
 assert.ok(Math.abs(r.batteryPower)<=c.batteryPower+1e-8&&r.fuelRemaining>=-1e-8);
 assert.ok(r.generatorPower===0||(r.generatorPower>=.3*r.generator-1e-8&&r.generatorPower<=r.generator+1e-8));
 if(!r.largeAvailable)assert.notEqual(r.generator,125);
 // critical load is shed last: any critical shortfall means every other priority was fully shed
 if(r.criticalUnserved>1e-8)close(r.essentialUnserved+r.domesticUnserved,r.load-r.criticalLoad);
 energy=r.energy;fuel=r.fuelRemaining;
 }}}
test('normal and disruption runs conserve energy, fuel and load priorities',()=>{for(const scenario of ['normal','blizzard','failure','miss'])physicalChecks(simulate({...defaults,days:15,scenario}));});
test('weather and demand come from the real record, with no random generator anywhere',()=>{
 const w=weather(defaults),H=STATION.history;
 for(let h=0;h<w.length;h+=97){close(w[h].temperature,STATION.temperature[h+H]);close(w[h].load,STATION.load[h+H]);close(w[h].pvBase,defaults.solar*STATION.pvPerKw[h+H]);close(w[h].windBase,defaults.wind*STATION.windPerKw[h+H]);}
 assert.match(STATION.source,/^ERA5/);
 const src=readFileSync(new URL('../dist/simulation.js',import.meta.url),'utf8');
 assert.doesNotMatch(src,/Math\.random|seed|noise\(/);
 assert.match(simulate({...defaults,days:1}).source,/^ERA5 reanalysis/);});
test('runs are reproducible and both controllers see identical measurements',()=>{const a=simulate({...defaults,days:3}),b=simulate({...defaults,days:3});assert.deepEqual(a.runs,b.runs);for(let h=0;h<72;h++)for(const k of ['load','pv','wind','temperature'])close(a.runs[0].rows[h][k],a.runs[1].rows[h][k]);});
test('blizzard lasts 96 hours and a missed lull leaves the forecast optimistic',()=>{const c={...defaults,scenario:'blizzard'},r=simulate(c);assert.equal(r.event.end-r.event.start,96);for(const row of r.runs[1].rows.slice(r.event.start,r.event.end)){close(row.pv,0);close(row.wind,0);}
 const m={...defaults,scenario:'miss'},e=simulate({...m,days:30}).event,ws=weather(m),w=ws[e.start+3],f=forecast(m,w,e.start,true,ws[e.start]);assert.ok(f.forecastExpected>w.pv+w.wind);});
test('generator fault and restoration force an immediate re-plan',()=>{const c={...defaults,days:6,scenario:'failure'},r=simulate(c);assert.ok(r.event.start>0);assert.equal(r.runs[1].rows[r.event.start].replanned,true);if(r.event.end<c.days*24)assert.equal(r.runs[1].rows[r.event.end].replanned,true);physicalChecks(r);});
test('every stress test costs the default station something, and the planner loses less',()=>{for(const scenario of ['blizzard','failure','miss']){const [rules,plan]=simulate({...defaults,scenario}).runs.map(x=>x.summary);assert.ok(rules.unserved>1,`${scenario}: rules lost ${rules.unserved} kWh`);assert.ok(plan.unserved<=rules.unserved+1e-6,`${scenario}: planner ${plan.unserved} vs rules ${rules.unserved}`);}});
test('empty resources expose critical shortfalls without NaN or fabricated power',()=>{const r=simulate({...defaults,days:1,solar:0,wind:0,battery:0,batteryPower:0,fuel:0});physicalChecks(r);for(const rr of r.runs){close(rr.summary.fuelUsed,0);assert.ok(rr.summary.criticalUnserved>0);assert.equal(rr.summary.criticalHours,24);for(const row of rr.rows)assert.ok(Object.values(row).filter(v=>typeof v==='number').every(Number.isFinite));}});
test('generator respects minimum loading when fuel cannot run it for a full hour',()=>{const c={...defaults,battery:0,batteryPower:0};const r=dispatch(c,{hour:0,load:90,pv:0,wind:0,largeAvailable:true},{energy:0,fuel:1},{unit:60,power:60});close(r.generatorPower,0);close(r.fuelRemaining,1);close(r.unserved,90);});
test('configuration rejects invalid inputs and exports all computed hours',()=>{assert.throws(()=>validateConfig({...defaults,days:0}));assert.throws(()=>validateConfig({...defaults,solar:NaN}));assert.throws(()=>validateConfig({...defaults,scenario:'unknown'}));assert.throws(()=>validateConfig({...defaults,days:1.5}));const r=simulate({...defaults,days:1});const csv=csvFor(r);assert.equal(csv.split('\n').length,49);assert.ok(csv.includes('"criticalUnserved"'));});
test('210-day horizon completes and preserves physical invariants',()=>{physicalChecks(simulate({...defaults,days:210,fuel:72000}));});
