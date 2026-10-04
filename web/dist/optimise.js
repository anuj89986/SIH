import {validateConfig, weather, summarize, eventWindow, MODEL_VERSION, DATA_SOURCE, START} from './simulation.js';
import {STATION} from './data/maitri-2023.js';

export const API_BASE = 'http://127.0.0.1:8000';
const RATINGS = [125, 60]; // API order follows engine/config/station.json.
const ETA = Math.sqrt(.90);
const timeAt = hour => new Date(Date.parse(START)+hour*3600000).toISOString();
export function requestFor(c, ws, state, diesel=true, forecastEnabled=true) {
  const tier = (w, name) => w.load * w[name] / (w.critical + w.essential + w.domestic);
  return {
    load_critical:ws.map(w=>tier(w,'critical')),
    load_essential:ws.map(w=>tier(w,'essential')),
    load_deferrable:ws.map(()=>0), // The record has no separate deferrable tier.
    load_sheddable:ws.map(w=>tier(w,'domestic')),
    renewable:ws.map(w=>w.pv+w.wind),
    reserve_req:ws.map(w=>forecastEnabled?0:diesel?w.load*(.05+.05*c.risk):0), // Legacy only; backend owns connected reserve.
    clean_air_penalty:ws.map(()=>0), // Legacy only; backend owns connected clean-air geometry.
    fuel_budget_l:state.fuel*ws.length/(c.days*24-ws[0].hour),
    soc0_kwh:state.energy, gen_on0:state.on, fuel_remaining_l:state.fuel,
    battery_capacity_kwh:c.battery, battery_power_kw:c.batteryPower,
    generator_available:[diesel&&ws[0].largeAvailable,diesel],
    ...(state.hours?{gen_state_hours0:state.hours}:{}),
    ...(forecastEnabled?{forecast:{start_time:timeAt(ws[0].hour),
      history:state.history||[{time:timeAt(ws[0].hour-1),load_kw:ws[0].prevLoad,pv_kw:ws[0].prevPv,wind_kw:ws[0].wind}],
      solar_capacity_kw:c.solar,wind_capacity_kw:c.wind}}:{}),
  };
}

export function rowsFromResponse(c, ws, state, result, request, take=request.forecast?1:ws.length) {
  if(!['Optimal','Feasible'].includes(result.status)) throw Error(`MILP returned ${result.status || 'an unknown status'}; no result was accepted.`);
  const n=ws.length;
  const vector=(a,name)=>{if(!Array.isArray(a)||a.length!==n||a.some(v=>!Number.isFinite(v)||v < -1e-5))throw Error(`Invalid backend response: ${name}.`);return a;};
  for(const k of ['power_kw','on','starts']) {
    if(result.generator?.[k]?.length!==2)throw Error(`Invalid backend generator ${k}.`);
    result.generator[k].forEach(a=>vector(a,`generator.${k}`));
  }
  for(const k of ['charge_kw','discharge_kw','soc_kwh'])vector(result.battery?.[k],`battery.${k}`);
  vector(result.curtailment_kw,'curtailment_kw');vector(result.fuel?.hourly_l,'fuel.hourly_l');
  for(const k of ['critical','essential','deferrable','sheddable'])vector(result.load_shed?.[k],`load_shed.${k}`);
  if(request.forecast){
    if(result.forecast_metadata?.method==null||!result.fuel?.hard_limit_enforced)throw Error('Backend lacks forecast or hard fuel support.');
    for(const target of ['load_kw','pv_kw','wind_kw','renewable_kw']){
      for(const q of ['p10','p50','p90'])vector(result.forecast?.[target]?.[q],`forecast.${target}.${q}`);
      for(let t=0;t<n;t++)if(result.forecast[target].p10[t]>result.forecast[target].p50[t]+1e-5||result.forecast[target].p50[t]>result.forecast[target].p90[t]+1e-5)throw Error('Invalid forecast quantile ordering.');
    }
    vector(result.reserve?.required_kw,'reserve.required_kw');vector(result.reserve?.shortfall_kw,'reserve.shortfall_kw');
    if(result.generator?.state_hours?.length!==2)throw Error('Invalid generator history.');
    result.generator.state_hours.forEach(a=>vector(a,'generator.state_hours'));
    if(result.clean_air?.sensitive?.length!==n)throw Error('Invalid clean-air metadata.');
    vector(result.clean_air.penalty_l_per_gen_h,'clean_air.penalty');
  }
  if(!Number.isFinite(result.fuel.total_l)||Math.abs(result.fuel.total_l-result.fuel.hourly_l.reduce((a,b)=>a+b,0))>1e-3)throw Error('Invalid backend fuel total.');
  let energy=state.energy,fuel=state.fuel;
  return ws.slice(0,take).map((w,t)=>{
    const on=result.generator.on.map(a=>a[t]),powers=result.generator.power_kw.map(a=>a[t]),starts=result.generator.starts.map(a=>a[t]);
    const charge=result.battery.charge_kw[t],discharge=result.battery.discharge_kw[t],next=result.battery.soc_kwh[t];
    const criticalUnserved=result.load_shed.critical[t],essentialUnserved=result.load_shed.essential[t],domesticUnserved=result.load_shed.deferrable[t]+result.load_shed.sheddable[t];
    const unserved=criticalUnserved+essentialUnserved+domesticUnserved,curtailed=result.curtailment_kw[t],generatorPower=powers.reduce((a,b)=>a+b,0),fuelUsed=result.fuel.hourly_l[t];
    const before=fuel;fuel-=fuelUsed;
    if(Math.abs(w.pv+w.wind+generatorPower+discharge-charge-(w.load-unserved+curtailed))>1e-3||Math.abs(next-(energy+ETA*charge-discharge/ETA))>1e-3||next<c.battery*.15-1e-3||next>c.battery*.95+1e-3||Math.max(charge,discharge)>c.batteryPower+1e-3||fuel < -1e-3)throw Error('Backend result violates power, battery or fuel limits.');
    for(let g=0;g<2;g++)if(![0,1].includes(on[g])||(!request.generator_available[g]&&(on[g]||powers[g]>1e-3))||powers[g]>RATINGS[g]*on[g]+1e-3||powers[g]<.3*RATINGS[g]*on[g]-1e-3)throw Error('Invalid backend generator commitment.');
    for(const k of ['critical','essential','deferrable','sheddable'])if(result.load_shed[k][t]>request[`load_${k}`][t]+1e-3)throw Error('Invalid backend load shedding.');
    energy=next;
    return {hour:w.hour,load:w.load,criticalLoad:request.load_critical[t],essentialLoad:request.load_essential[t],domesticLoad:request.load_sheddable[t],pv:w.pv,wind:w.wind,temperature:w.temperature,
      generator:RATINGS.reduce((a,v,g)=>a+v*on[g],0),generatorPower,generatorPowerByUnit:powers,generatorOn:on,generatorStarts:starts,
      generatorStateHours:result.generator.state_hours?.map(a=>a[t]),
      batteryPower:discharge-charge,batteryCharge:charge,batteryDischarge:discharge,energy:next,soc:c.battery?next/c.battery*100:0,
      fuelUsed,fuelRemaining:Math.max(0,fuel),unserved,criticalUnserved,essentialUnserved,domesticUnserved,deferrableUnserved:result.load_shed.deferrable[t],sheddableUnserved:result.load_shed.sheddable[t],curtailed,
      losses:charge*(1-ETA)+discharge*(1/ETA-1),disruption:w.disruption,largeAvailable:w.largeAvailable,controller:'Python MILP',replanned:t===0,protected:false,
      forecastExpected:result.forecast?.renewable_kw.p50[t]??w.pv+w.wind,forecastLow:result.forecast?.renewable_kw.p10[t]??w.pv+w.wind,forecastHigh:result.forecast?.renewable_kw.p90[t]??w.pv+w.wind,
      forecastLoad:result.forecast?.load_kw.p50[t]??w.load,forecastErrorRenewable:result.forecast?w.pv+w.wind-result.forecast.renewable_kw.p50[t]:null,
      forecastErrorLoad:result.forecast?w.load-result.forecast.load_kw.p50[t]:null,forecastMethod:result.forecast_metadata?.method,
      reserveReq:result.reserve?.required_kw[t]??request.reserve_req[t],reserveShortfall:result.reserve?.shortfall_kw[t]??0,reserveEnergy:0,
      cleanAirSensitive:result.clean_air?.sensitive[t]??false,cleanAirPenalty:result.clean_air?.penalty_l_per_gen_h[t]??0,
      dieselDiscouraged:result.clean_air?.diesel_discouraged[t]??false,dieselAvoided:result.clean_air?.diesel_avoided[t]??false,
      emergencyHistoryOverride:result.generator.emergency_history_override??false,
      budgetRemaining:c.fuel*(w.hour+1)/(c.days*24)-(c.fuel-fuel),dailyAllowance:before/Math.max(1,(c.days*24-w.hour)/24),
      reason:`Python MILP ${result.forecast?'executes current measurements with a forecast planning horizon':'dispatches legacy client series'}. ${unserved>1e-6?'Available resources leave unmet demand.':generatorPower?'Diesel, renewables and battery meet demand.':'Renewables and battery meet demand.'} ${result.clean_air?.sensitive[t]?'Clean-air penalty discourages diesel; reliability has priority.':''} ${result.reserve?.shortfall_kw[t]>1e-4?'Requested reserve is partly unavailable.':''}`};
  });
}

async function apiJson(path, body, {base, fetchImpl, signal, timeoutMs}) {
  signal?.throwIfAborted();
  let response;
  try { response=await fetchImpl(`${base}${path}`,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined,signal:AbortSignal.any([...(signal?[signal]:[]),AbortSignal.timeout(timeoutMs)])}); }
  catch(e){if(signal?.aborted)throw signal.reason;throw Error(`Backend unavailable at ${base} (offline, CORS blocked or request timed out). Start/restart FastAPI and retry.`);}
  if(!response.ok){let detail;try{detail=(await response.json()).detail;}catch{}throw Error(`Backend HTTP ${response.status}: ${typeof detail==='string'?detail:JSON.stringify(detail||response.statusText)}`);}
  try{return await response.json();}catch{throw Error('Backend returned invalid JSON.');}
}

export async function optimiseMission(config, baseline, progress=()=>{}, options={}) {
  const c=validateConfig(config),total=c.days*24,ws=weather(c),rows=[],solves=[];
  const opts={base:API_BASE,fetchImpl:fetch,timeoutMs:15000,...options};
  const health=await apiJson('/api/health',null,{...opts,timeoutMs:3000});
  if(health.web_integration!==1)throw Error('Restart FastAPI to load the frontend integration in engine/api.py.');
  const connected=health.capabilities?.forecast===true&&health.capabilities?.hard_fuel_limit===true;
  const history=Array.from({length:STATION.history},(_,i)=>({time:timeAt(i-STATION.history),load_kw:STATION.load[i],pv_kw:c.solar*STATION.pvPerKw[i],wind_kw:c.wind*STATION.windPerKw[i]}));
  let state={energy:c.battery*c.initialSoc/100,fuel:c.fuel,on:[0,0],history};
  for(let h=0;h<total;) {
    opts.signal?.throwIfAborted();progress({controller:'Python MILP',hour:h,total});
    let n=Math.min(connected?(health.default_horizon_h||24):24,total-h);
    // An outage/restoration is a boundary: availability is constant in each request.
    for(let t=1;t<n;t++)if(ws[h+t].largeAvailable!==ws[h].largeAvailable){n=t;break;}
    // Smallest possible one-hour burn: 60 kW set, 30% loading (station.json).
    let diesel=connected||state.fuel>=.084*60+.252*18-1e-5,result,request;
    while(true) {
      request=requestFor(c,ws.slice(h,h+n),state,diesel,connected);
      result=await apiJson('/api/optimise',request,opts);
      // Only an older backend needs horizon repair. New results must obey the tank.
      if(!['Optimal','Feasible'].includes(result.status))throw Error(`MILP returned ${result.status}; previous results retained.`);
      if(!Number.isFinite(result.fuel?.total_l))throw Error('Invalid backend fuel total.');
      if(result.fuel.total_l<=state.fuel+1e-5)break;
      if(connected||result.fuel.hard_limit_enforced)throw Error('Backend result violates physical tank limit.');
      if(n>1)n=Math.max(1,Math.floor(n/2));
      else if(diesel)diesel=false;
      else throw Error('Backend consumed fuel with diesel disabled.');
    }
    const chunk=rowsFromResponse(c,ws.slice(h,h+n),state,result,request);rows.push(...chunk);
    solves.push({hour:h,horizon:n,executed:chunk.length,status:result.status,solver:result.solver,solveTime:result.solve_time_s,objective:result.objective,overBudget:result.fuel.over_budget_l,
      forecast:result.forecast,forecastMetadata:result.forecast_metadata,reserve:result.reserve,cleanAir:result.clean_air,optimizerInputs:result.optimizer_inputs});
    for(const w of ws.slice(h,h+chunk.length))history.push({time:timeAt(w.hour),load_kw:w.load,pv_kw:w.pv,wind_kw:w.wind});
    if(history.length>72)history.splice(0,history.length-72);
    const last=chunk.at(-1);state={energy:last.energy,fuel:last.fuelRemaining,on:last.generatorOn,hours:last.generatorStateHours,history};h+=chunk.length;
  }
  return {modelVersion:MODEL_VERSION,createdAt:new Date().toISOString(),source:DATA_SOURCE,config:c,event:eventWindow(c),execution:'python-milp',api:opts.base,
    method:connected?`${health.default_horizon_h||24}-hour quantile forecast MPC (backend supports up to ${health.max_horizon_h||36} h), hourly measured execution; uncertainty reserve; soft clean-air penalty; hard tank + soft seasonal allowance; elapsed generator state carried. Synthetic ERA5 NWP, not operational telemetry.`:'Legacy client-series MILP with defensive fuel repair.',solves,
    runs:[baseline,{controller:'Python MILP',rows,summary:summarize(rows,c)}]};
}
