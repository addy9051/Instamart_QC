const products = [
  { name: 'Lays Magic Masala · 52 g', sku: '8901491501054', qty: 2, weight: 52, emoji: '🍟', color: '#e4bc37', variant: 'Magic Masala' },
  { name: 'Amul Taaza milk · 500 ml', sku: '8901262150012', qty: 1, weight: 515, emoji: '🥛', color: '#f0e9db', variant: 'Taaza' },
  { name: 'Fresh tomatoes · 500 g', sku: 'FNV-VEG-081', qty: 1, weight: 500, emoji: '🍅', color: '#d75a45', variant: 'Tomatoes' },
  { name: 'Amul paneer · 200 g', sku: '8901262171093', qty: 1, weight: 205, emoji: '🧀', color: '#e6c47a', variant: 'Paneer' },
];
const state = { counts: products.map(() => 0), weight: 0, audited: 0, issues: 0, review: 0, issueOnOrder: false, drops: 0 };
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
function renderItems() {
  const list = $('#item-list'); let verified = 0;
  list.innerHTML = products.map((p, i) => {
    const done = Math.min(state.counts[i], p.qty); verified += done;
    const flagged = state.counts[i] > p.qty;
    const rowState = flagged ? 'flagged' : done === p.qty ? 'verified' : '';
    const mark = flagged ? '!' : done === p.qty ? '✓' : '·';
    return `<div class="item-row ${rowState}"><div class="item-thumb">${p.emoji}</div><div class="item-copy"><b>${p.name}</b><small>SKU ${p.sku}</small></div><span class="item-qty">${done}/${p.qty}</span><span class="item-check">${mark}</span></div>`;
  }).join('');
  $('#item-progress').textContent = `${verified} / ${products.reduce((n,p)=>n+p.qty,0)} added to bag`;
  $('#weight-total').textContent = `${state.weight.toLocaleString('en-IN')} g`;
}
function toast(message) { const t=$('#toast'); t.textContent=message; t.classList.add('show'); clearTimeout(toast.timer); toast.timer=setTimeout(()=>t.classList.remove('show'),2300); }
function updateSignals(vision, weight, labelVision, labelWeight, alert=false) {
  $('#vision-signal').textContent=labelVision; $('#scale-signal').textContent=labelWeight;
  $('#vision-meter').style.width=`${Math.max(4,Math.min(100,vision))}%`; $('#scale-meter').style.width=`${Math.max(4,Math.min(100,weight))}%`;
  $('#vision-meter').style.background=alert?'#dc8067':'#52b576'; $('#scale-meter').style.background=alert?'#dc8067':'#52b576';
}
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}
async function runApiAudit(expected, scenario, observedWeight) {
  const isWrong = scenario === 'wrong', isExtra = scenario === 'extra';
  const photo = $('#packing-photo')?.files[0];
  const payload = {
    event_id: `ui-${state.drops}`, order_id: 'IM-804921', expected_sku: expected.sku,
    expected_unit_weight_g: expected.weight, expected_quantity: 1, observed_quantity: isExtra ? 2 : 1,
    measured_weight_delta_g: isExtra ? expected.weight * 2 : observedWeight,
    observed_sku: isWrong ? '8901491501061' : expected.sku,
  };
  if (photo) payload.crop_base64 = await fileToBase64(photo);
  const started = performance.now();
  let res;
  try {
    const response = await fetch('/v1/packing/audit', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    res = await response.json();
  } catch (error) {
    toast(`Audit API unreachable: ${error.message}`);
    return;
  }
  $('#stat-latency').firstChild.textContent = Math.round(performance.now() - started);
  state.audited++; $('#stat-audited').textContent = state.audited;
  if (res.decision === 'hold') { state.issues++; $('#stat-issues').textContent = state.issues; }
  if (res.decision === 'review_required') { state.review++; $('#stat-review').textContent = state.review; }
  const flagged = res.decision === 'hold';
  const similarity = res.visual_similarity;
  $('#event-title').textContent = `Audit decision: ${res.decision}`;
  $('#event-time').textContent = new Intl.DateTimeFormat('en-IN', {hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false}).format(new Date());
  $('#event-content').innerHTML = `<div class="event-detail"><div class="event-badge ${flagged ? 'flag' : 'pass'}">${flagged ? '!' : (res.decision === 'pass' ? 'OK' : '?')}</div><div class="event-copy"><b>${expected.name}</b><small>reasons: ${res.reason_codes.join(', ')} | inputs: scenario "${scenario}", manual weight ${payload.measured_weight_delta_g} g, ${photo ? 'photo supplied' : 'no photo, vision not run'}</small></div><span class="event-flag ${flagged ? 'flag' : ''}">${res.decision.toUpperCase()}</span></div>`;
  updateSignals(similarity == null ? 0 : similarity * 100, Math.max(0, 100 - res.weight_error_pct),
    similarity == null ? 'not run (no photo)' : `${(similarity * 100).toFixed(1)}%`, `${res.weight_error_pct}% off nominal`, flagged);
  toast(`${res.decision}: ${res.reason_codes.join(', ')}`);
}
function audit(kind) {
  let idx = state.counts.findIndex((n,i)=>n<products[i].qty); if(idx<0) idx=0;
  const expected=products[idx]; const isWrong=kind==='wrong'; const isExtra=kind==='extra';
  const p = isWrong ? {...expected, name: expected.name.replace(expected.variant, expected.variant==='Magic Masala'?'Classic Salted':'Wrong variant'), variant: expected.variant==='Magic Masala'?'Classic Salted':'Wrong variant', emoji: expected.emoji, color:'#dba948'} : expected;
  const observedWeight = isExtra ? expected.weight : Math.round(expected.weight * (isWrong ? 1.02 : 1 + ((state.drops%3)-1)*.006));
  state.drops++;
  if (!isWrong || isExtra) { state.counts[idx]++; state.weight+=observedWeight; }
  const flagged=isWrong||isExtra;
  renderItems();
  const frame=$('.camera-frame'); frame.classList.remove('scanning'); void frame.offsetWidth; frame.classList.add('scanning');
  if(!isWrong){const item=document.createElement('div');item.className='bag-product';item.style.background=p.color;item.textContent=p.emoji;$('#bag-items .bag-empty')?.remove();$('#bag-items').appendChild(item);}
  runApiAudit(expected, kind, observedWeight);
}
function resetOrder() { state.counts=products.map(()=>0);state.weight=0;state.issueOnOrder=false;state.drops=0;renderItems();$('#bag-items').innerHTML='<div class="bag-empty">DROP ITEMS<br>TO BEGIN</div>';$('#event-title').textContent='Waiting for first item';$('#event-time').textContent='—';$('#event-content').innerHTML='<div class="event-empty"><span>↗</span>Drop an item to see the vision and weight checks.</div>';updateSignals(0,0,'—','—');toast('Order reset · ready for next pack'); }
$('#drop-correct').addEventListener('click',()=>audit('correct'));
$('#drop-wrong').addEventListener('click',()=>audit('wrong'));
$('#drop-extra').addEventListener('click',()=>audit('extra'));
$('#reset-order').addEventListener('click',resetOrder);
$('#clear-bag').addEventListener('click',resetOrder);

$('#match-catalog-image').addEventListener('click', async () => {
  const file=$('#catalog-image').files?.[0];
  if(!file){toast('Choose a product photo first');return;}
  const button=$('#match-catalog-image'), output=$('#catalog-match-result');
  button.disabled=true;button.textContent='Analyzing…';output.textContent='Creating a DINOv2 embedding and searching the SKU reference bank…';
  try{
    const bitmap=await createImageBitmap(file);const maxSide=1024;const ratio=Math.min(1,maxSide/Math.max(bitmap.width,bitmap.height));
    const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(bitmap.width*ratio));canvas.height=Math.max(1,Math.round(bitmap.height*ratio));
    canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);bitmap.close();
    const imageBase64=canvas.toDataURL('image/jpeg',.84).split(',')[1];
    const response=await fetch('/v1/catalog/match',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image_base64:imageBase64})});
    const result=await response.json();if(!response.ok)throw new Error(result.detail||`HTTP ${response.status}`);
    if(result.top_sku){output.innerHTML=`<span class="match-result-state ${result.decision==='match'?'pass':'review'}">${result.decision==='match'?'MATCH':'REVIEW'}</span><b>${result.top_sku}</b><span>Cosine similarity ${(result.similarity*100).toFixed(1)}% · threshold ${(result.threshold*100).toFixed(0)}%</span>`;}
    else{output.innerHTML=`<span class="match-result-state review">REVIEW</span><span>${result.reason_codes.join(', ')}</span>`;}
  }catch(error){output.textContent=`Could not reach the model service: ${error.message}`;}
  finally{button.disabled=false;button.textContent='Analyze image →';}
});

$('#analyze-freshness').addEventListener('click', async () => {
  const file = $('#freshness-image').files?.[0];
  const button = $('#analyze-freshness'), output = $('#freshness-summary');
  if (!file) { toast('Choose a produce image first'); return; }
  button.disabled = true; button.textContent = 'Analyzing…';
  output.textContent = 'Running the trained freshness classifier…';
  try {
    const bitmap = await createImageBitmap(file), maxSide = 1024;
    const ratio = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * ratio));
    canvas.height = Math.max(1, Math.round(bitmap.height * ratio));
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height); bitmap.close();
    const imageBase64 = canvas.toDataURL('image/jpeg', .84).split(',')[1];
    const response = await fetch('/v1/inward/freshness', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({sku:$('#inward-sku').value, image_base64:imageBase64})});
    const result = await response.json(); if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
    const label = (result.class_name || 'unknown').replaceAll('_',' ');
    const badge = result.decision.replaceAll('_',' ').toUpperCase();
    output.innerHTML = `<span class="match-result-state ${result.decision==='accept'?'pass':'review'}">${badge}</span><b>${label}</b><span>${result.confidence===null?'No model confidence':`Confidence ${(result.confidence*100).toFixed(1)}%`} · ${result.reason_codes.join(', ')}</span>`;
  } catch (error) { output.textContent = `Could not run freshness model: ${error.message}`; }
  finally { button.disabled = false; button.textContent = 'Analyze freshness →'; }
});

$$('.nav-item').forEach(btn=>btn.addEventListener('click',()=>{
  const page=btn.dataset.page; $$('.nav-item').forEach(b=>b.classList.toggle('active',b===btn)); $$('.page').forEach(p=>p.classList.toggle('active',p.id===`page-${page}`));
  const titles={packing:'PACKING STATION',inward:'INWARD QUALITY',economics:'LOSS & ROI'};$('#crumb-title').textContent=titles[page];window.scrollTo({top:0,behavior:'smooth'});
}));

function updateRemaining() {
  const received=new Date(`${$('#received-date').value}T00:00:00`), expiry=new Date(`${$('#expiry-date').value}T00:00:00`);
  let days=Math.ceil((expiry-received)/86400000);if(!Number.isFinite(days))days=0;days=Math.max(-999,days);
  const totalDays=$('#inward-sku').value.includes('Milk')?7:$('#inward-sku').value.includes('Paneer')?14:$('#inward-sku').value.includes('tomatoes')?7:7;
  const pct=Math.round(days/totalDays*100);$('#remaining-days').innerHTML=`${days} day${Math.abs(days)===1?'':'s'} <span>(${pct}%)</span>`;$('#life-bar').style.width=`${Math.max(0,Math.min(100,pct))}%`;
  const packaged=$('#inward-sku').value.includes('tomatoes');const threshold=packaged?50:70;$('#policy-threshold').textContent=`${threshold}%`;
  $('#life-bar').style.background=pct>=threshold?'#55b778':'#dda33c';return {days,pct,threshold};
}
$('#received-date').addEventListener('change',updateRemaining);$('#expiry-date').addEventListener('change',updateRemaining);$('#inward-sku').addEventListener('change',updateRemaining);
$('#run-inward').addEventListener('click',()=>{
  const {days,pct,threshold}=updateRemaining(); const freshness=$('.produce-card.active')?.dataset.freshness||'Good';
  let pass=pct>=threshold && days>=0 && freshness==='Good'; let title=pass?'Accept batch':pct<0?'Expired batch':pct<threshold?'Shelf-life below policy':`${freshness} sample · review required`;
  let cls=pass?'pass':freshness==='Review'?'':'fail'; let detail=pass?'Expiry and condition are within the configured receiving checks.':freshness==='Review'?'Condition is uncertain. Route this sample to a receiving associate.':freshness==='Reject'?'Sample marked spoiled. Keep the batch out of pickable inventory.':`Received with ${pct}% remaining shelf life; policy requires ${threshold}%.`;
  $('#inward-result').innerHTML=`<div class="decision-result"><span class="decision-badge ${pass?'pass':''}">${pass?'ACCEPT':'HOLD FOR REVIEW'}</span><div class="decision-header"><div class="decision-icon ${cls}">${pass?'✓':'!'}</div><div><h2>${title}</h2><p>${detail}</p></div></div><div class="decision-details"><div><small>DATE ENTERED (MANUAL)</small><b>${new Date(`${$('#expiry-date').value}T00:00:00`).toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'})}</b></div><div><small>REMAINING LIFE</small><b>${pct}% · ${days} days</b></div><div><small>BATCH</small><b>${$('#batch-id').value||'—'}</b></div><div><small>FRESHNESS SAMPLE</small><b>${freshness}</b></div></div></div>`;
  toast(pass?'Receiving check passed':'Receiving check held for review');
});
$$('.produce-card').forEach(card=>card.addEventListener('click',()=>{
  $$('.produce-card').forEach(c=>c.classList.toggle('active',c===card));const v=card.dataset.freshness;const copy={Good:'Good condition',Review:'Needs a human review',Reject:'Marked for rejection'};$('#freshness-summary').innerHTML=`<span class="good-mark">${v==='Good'?'✓':'!'}</span> Produce sample marked <b>${copy[v]}</b>`;
}));

const defaults={orders:1200,aov:700,refund:2,reduction:25,hardware:35000,stores:100};
const inr=n=>'₹'+Math.round(n).toLocaleString('en-IN');
const compact=n=>n>=1e7?`₹${(n/1e7).toFixed(2)}Cr`:n>=1e5?`₹${(n/1e5).toFixed(2)}L`:inr(n);
function calcEconomics(){
 const orders=+$('#orders-slider').value,aov=+$('#aov-slider').value,refund=+$('#refund-slider').value/100,reduction=+$('#reduction-slider').value/100,hardware=+$('#hardware-slider').value,stores=+$('#stores-slider').value;
 $('#orders-output').textContent=orders.toLocaleString('en-IN');$('#aov-output').textContent=`₹${aov}`;$('#refund-output').textContent=`${(refund*100).toFixed(1)}%`;$('#reduction-output').textContent=`${Math.round(reduction*100)}%`;$('#hardware-output').textContent=inr(hardware);$('#stores-output').textContent=stores.toLocaleString('en-IN');
 const monthly=orders*aov*refund*reduction*30;const annual=monthly*12;const payback=monthly?hardware/monthly:Infinity;const capex=hardware*stores;const network=annual*stores;
 $('#savings-store').innerHTML=`${inr(monthly)}<span>/mo</span>`;$('#annual-store').textContent=compact(annual);$('#rollout-capex').textContent=compact(capex);$('#rollout-count').textContent=`${stores.toLocaleString('en-IN')} ${stores===1?'store':'stores'}`;$('#network-savings').textContent=compact(network);$('#payback').textContent=Number.isFinite(payback)?`${payback.toFixed(1)} months`:'—';$('#payback-bar').style.width=`${Math.max(4,Math.min(100,6/payback*100))}%`;
}
$$('.assumption input').forEach(el=>el.addEventListener('input',calcEconomics));
$('#reset-economics').addEventListener('click',()=>{Object.entries(defaults).forEach(([k,v])=>{const map={orders:'orders-slider',aov:'aov-slider',refund:'refund-slider',reduction:'reduction-slider',hardware:'hardware-slider',stores:'stores-slider'};$('#'+map[k]).value=v;});calcEconomics();});
const now=new Date();$('#today').textContent=new Intl.DateTimeFormat('en-IN',{weekday:'short',day:'2-digit',month:'short',year:'numeric'}).format(now).toUpperCase();$('#sync-time').textContent=new Intl.DateTimeFormat('en-IN',{hour:'2-digit',minute:'2-digit',hour12:false}).format(now);renderItems();updateRemaining();calcEconomics();
