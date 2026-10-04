import test from 'node:test';
import assert from 'node:assert/strict';
import {defaults,weather,simulate,csvFor} from '../dist/simulation.js';
import {requestFor,rowsFromResponse,optimiseMission} from '../dist/optimise.js';

const state={energy:260,fuel:6300,on:[0,0]};
test('request uses tiered weather loads, capacities, reserve, SOC, fuel and API generator order',()=>{
 const c={...defaults,days:1},ws=weather(c).slice(0,24),q=requestFor(c,ws,state);
 assert.equal(q.soc0_kwh,260);assert.equal(q.battery_power_kw,100);assert.deepEqual(q.gen_on0,[0,0]);
 assert.deepEqual(q.generator_available,[true,true]);assert.equal(q.fuel_budget_l,6300);
 for(let i=0;i<24;i++){
  assert.ok(Math.abs(q.load_critical[i]+q.load_essential[i]+q.load_sheddable[i]-ws[i].load)<1e-7);
  assert.equal(q.load_deferrable[i],0);assert.equal(q.renewable[i],ws[i].pv+ws[i].wind);
  assert.equal(q.reserve_req[i],0);assert.equal(q.clean_air_penalty[i],0);
 }
 assert.equal(q.forecast.start_time,'2023-05-01T00:00:00.000Z');assert.ok(q.forecast.history.every(r=>Date.parse(r.time)<Date.parse(q.forecast.start_time)));
 assert.equal(requestFor(c,ws,state,true,false).reserve_req[0],ws[0].load*.1);
 assert.deepEqual(requestFor(c,ws,{...state,hours:[1,5]}).gen_state_hours0,[1,5]);
 assert.deepEqual(requestFor(c,[{...ws[0],largeAvailable:false}],state).generator_available,[false,true]);
 assert.deepEqual(requestFor(c,ws,state,false).generator_available,[false,false]);
});
test('offline, HTTP errors, timeout and cancellation remain distinguishable',async()=>{
 const c={...defaults,days:1};
 await assert.rejects(optimiseMission(c,null,()=>{},{fetchImpl:async()=>{throw new TypeError('fetch failed');}}),/Backend unavailable/);
 await assert.rejects(optimiseMission(c,null,()=>{},{fetchImpl:async()=>new Response(JSON.stringify({detail:'bad input'}),{status:422})}),/HTTP 422: bad input/);
 await assert.rejects(optimiseMission(c,null,()=>{},{fetchImpl:async()=>{throw new DOMException('Timeout','TimeoutError');}}),/timed out/);
 const abort=new AbortController();abort.abort();
 await assert.rejects(optimiseMission(c,null,()=>{},{signal:abort.signal}),{name:'AbortError'});
 await assert.rejects(optimiseMission(c,null,()=>{},{fetchImpl:async()=>new Response(JSON.stringify({status:'healthy'}))}),/Restart FastAPI/);
});

const base=process.env.POLAREMS_TEST_API;
function physical(run){for(const r of run.runs[1].rows){
 assert.ok(Math.abs(r.pv+r.wind+r.generatorPower+r.batteryPower-r.load+r.unserved-r.curtailed)<1e-3);
 assert.ok(r.fuelRemaining>=0);assert.ok(r.energy>=run.config.battery*.15-1e-3&&r.energy<=run.config.battery*.95+1e-3);
 assert.ok(Math.abs(r.batteryPower)<=run.config.batteryPower+1e-3);
 assert.equal(r.generatorPower,r.generatorPowerByUnit.reduce((a,b)=>a+b,0));
 if(!r.largeAvailable)assert.equal(r.generatorOn[0],0);
}}
test('live API: full 30-day Run Simulation mapping and exports',{skip:!base},async()=>{
 const browser=simulate(defaults);const run=await optimiseMission(defaults,browser.runs[0],()=>{},{base});
 assert.equal(run.execution,'python-milp');assert.equal(run.runs[1].rows.length,720);assert.equal(run.runs[0],browser.runs[0]);
 assert.equal(csvFor(run).split('\n').length,1441);physical(run);
 const ws=weather(defaults).slice(0,24),q=requestFor(defaults,ws,state);
 const result=await (await fetch(base+'/api/optimise',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(q)})).json();
 assert.equal(rowsFromResponse(defaults,ws,state,result,q).length,1);
 assert.equal(rowsFromResponse(defaults,ws,state,{...result,status:'Feasible'},q).length,1);
 assert.equal(run.solves.length,720);assert.ok(run.solves.every(s=>s.executed===1&&s.horizon<=36));
 assert.ok(run.runs[1].rows.some(r=>r.cleanAirSensitive&&r.cleanAirPenalty>0));
 assert.ok(run.runs[1].rows.every(r=>r.generatorStateHours?.length===2&&r.forecastMethod==='lightgbm-quantile'));
 assert.throws(()=>rowsFromResponse(defaults,ws,state,{...result,status:'Infeasible'},q),/MILP returned/);
 assert.throws(()=>rowsFromResponse(defaults,ws,state,{...result,battery:{...result.battery,soc_kwh:[]}},q),/Invalid backend response/);
});
test('live API: outage/restoration, blizzard, missed lull and custom battery settings',{skip:!base},async()=>{
 for(const scenario of ['failure','blizzard','miss']){
  const c={...defaults,days:3,scenario,battery:200,batteryPower:30};
  const run=await optimiseMission(c,simulate(c).runs[0],()=>{},{base});physical(run);
  assert.equal(run.runs[1].rows.length,72);
  if(scenario==='failure')assert.ok(run.runs[1].rows.some(r=>!r.largeAvailable));
  if(scenario==='blizzard')assert.ok(run.runs[1].rows.some(r=>r.pv+r.wind===0));
 }
});
test('live API: empty and almost empty tank cannot fabricate fuel or power',{skip:!base},async()=>{
 for(const fuel of [0,10,100]){
  const c={...defaults,days:1,solar:0,wind:0,battery:0,batteryPower:0,fuel};
  const run=await optimiseMission(c,simulate(c).runs[0],()=>{},{base});physical(run);
  assert.ok(run.runs[1].summary.fuelUsed<=fuel+1e-3);assert.ok(run.runs[1].summary.criticalUnserved>0);
 }
});
test('live API: CORS permits both local origins and malformed inputs are rejected',{skip:!base},async()=>{
 for(const origin of ['http://localhost:3000','http://127.0.0.1:3000','http://localhost:5500']){
  const response=await fetch(base+'/api/optimise',{method:'OPTIONS',headers:{Origin:origin,'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'content-type'}});
  assert.equal(response.status,200);assert.equal(response.headers.get('access-control-allow-origin'),origin);
 }
 const request=requestFor(defaults,weather(defaults).slice(0,6),state);
 for(const change of [{load_critical:[]},{gen_on0:[0]},{generator_available:[true]},{soc0_kwh:9999},{renewable:[-1,0,0,0,0,0]}]){
  const response=await fetch(base+'/api/optimise',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...request,...change})});assert.equal(response.status,400);
 }
});
