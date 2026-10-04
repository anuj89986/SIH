import {simulate} from './simulation.js';
self.onmessage=e=>{try{const run=simulate(e.data,p=>self.postMessage({type:'progress',...p}));self.postMessage({type:'result',run});}catch(error){self.postMessage({type:'error',message:error.message});}};
