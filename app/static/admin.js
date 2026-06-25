(function(){
  const EMPTY='داده کافی برای نمایش نمودار وجود ندارد.';
  const charts=new Map();
  const fetchJSON=url=>fetch(url,{credentials:'same-origin'}).then(r=>{if(!r.ok)throw new Error('HTTP '+r.status);return r.json();});
  const hasData=arr=>Array.isArray(arr)&&arr.some(v=>Number(v||0)>0);
  const showEmpty=(el,msg=EMPTY)=>{if(el)el.innerHTML=`<div class="empty">${msg}</div>`};
  const destroy=id=>{if(charts.has(id)){charts.get(id).destroy();charts.delete(id);}};
  const render=(el,opts)=>{if(!el||!window.ApexCharts)return;destroy(el.id);const c=new ApexCharts(el,opts);charts.set(el.id,c);c.render();};
  const colors=['#2563eb','#0f766e','#7c3aed','#d97706','#be123c','#475569'];
  const donut=(el,labels,series)=>hasData(series)?render(el,{chart:{type:'donut',height:280,fontFamily:'Tahoma'},labels,series,colors,legend:{position:'bottom'}}):showEmpty(el);
  const bar=(el,cats,data)=>hasData(data)?render(el,{chart:{type:'bar',height:280,toolbar:{show:false},fontFamily:'Tahoma'},series:[{name:'تعداد',data}],xaxis:{categories:cats},colors:[colors[0]]}):showEmpty(el);
  const toman=v=>(Number(v||0).toLocaleString('fa-IR')+' تومان');

  function initAnalytics(){
    const root=document.querySelector('.chart-grid[data-range]'); if(!root||!window.ApexCharts)return; const range=root.dataset.range||'30d';
    fetchJSON(`/admin/api/analytics/revenue?range=${range}`).then(d=>{const e=document.querySelector('#revenueTrend'); if(e)hasData(d.revenue)?render(e,{chart:{type:'line',height:300,toolbar:{show:false},fontFamily:'Tahoma'},series:[{name:'درآمد تاییدشده',data:d.revenue}],xaxis:{categories:d.labels},yaxis:{labels:{formatter:toman}},colors:[colors[0]],stroke:{curve:'smooth',width:3}}):showEmpty(e); donut(document.querySelector('#receiptStatus'),['در انتظار','تایید','رد'],[d.funnel.pending,d.funnel.approved,d.funnel.rejected]); bar(document.querySelector('#revenueByPlan'),Object.keys(d.by_plan||{}),Object.values(d.by_plan||{}));}).catch(()=>showEmpty(root,'خطا در دریافت داده‌های آنالیتیکس.'));
    fetchJSON(`/admin/api/analytics/users?range=${range}`).then(d=>{const e=document.querySelector('#usersTrend'); if(e)(hasData(d.new_users)||hasData(d.active_users))?render(e,{chart:{type:'line',height:300,toolbar:{show:false},fontFamily:'Tahoma'},series:[{name:'کاربر جدید',data:d.new_users},{name:'کاربر فعال',data:d.active_users}],xaxis:{categories:d.labels},colors:[colors[2],colors[1]],stroke:{curve:'smooth'}}):showEmpty(e); donut(document.querySelector('#planDistribution'),Object.keys(d.plan_distribution||{}),Object.values(d.plan_distribution||{}));}).catch(()=>{});
    fetchJSON(`/admin/api/analytics/behavior?range=${range}`).then(d=>{const e=document.querySelector('#messageVolume'); if(e)(hasData(d.messages)||hasData(d.voice)||hasData(d.stickers))?render(e,{chart:{type:'bar',height:300,stacked:true,toolbar:{show:false},fontFamily:'Tahoma'},series:[{name:'پیام',data:d.messages},{name:'وویس',data:d.voice},{name:'استیکر',data:d.stickers}],xaxis:{categories:d.labels},colors:[colors[0],colors[2],colors[3]]}):showEmpty(e);}).catch(()=>{});
    fetchJSON(`/admin/api/analytics/partners?range=${range}`).then(d=>{bar(document.querySelector('#relationshipStages'),Object.keys(d.relationship_stage||{}),Object.values(d.relationship_stage||{})); donut(document.querySelector('#partnerGender'),Object.keys(d.partner_gender||{}),Object.values(d.partner_gender||{})); bar(document.querySelector('#moodDistribution'),Object.keys(d.mood||{}),Object.values(d.mood||{})); bar(document.querySelector('#relationshipDepth'),Object.keys(d.depth||{}),Object.values(d.depth||{}));}).catch(()=>{});
    fetchJSON(`/admin/api/analytics/proactive?range=${range}`).then(d=>{const e=document.querySelector('#proactiveChart'); if(e)(hasData(d.scheduled)||hasData(d.sent)||hasData(d.skipped)||hasData(d.replied))?render(e,{chart:{type:'bar',height:280,toolbar:{show:false},fontFamily:'Tahoma'},series:[{name:'زمان‌بندی',data:d.scheduled},{name:'ارسال',data:d.sent},{name:'رد/اسکیپ',data:d.skipped},{name:'پاسخ',data:d.replied}],xaxis:{categories:d.labels},colors:[colors[0],colors[1],colors[3],colors[2]]}):showEmpty(e);}).catch(()=>{});
    fetchJSON(`/admin/api/analytics/support?range=${range}`).then(d=>{const e=document.querySelector('#supportChart'); if(e)(hasData(d.opened)||hasData(d.replied))?render(e,{chart:{type:'line',height:280,toolbar:{show:false},fontFamily:'Tahoma'},series:[{name:'درخواست',data:d.opened},{name:'پاسخ',data:d.replied}],xaxis:{categories:d.labels},colors:[colors[0],colors[1]]}):showEmpty(e);}).catch(()=>{});
  }

  function initUserCharts(){
    const root=document.querySelector('[data-user-detail]'); if(!root||!window.ApexCharts)return; const id=root.dataset.userId;
    fetchJSON(`/admin/api/users/${id}/activity?range=7d`).then(d=>{
      const act=document.querySelector('#userActivityTrend');
      const total=d.messages?.total||[];
      if(act)hasData(total)?render(act,{chart:{type:'area',height:280,toolbar:{show:false},fontFamily:'Tahoma'},series:[{name:'کاربر',data:d.messages.user},{name:'بات',data:d.messages.assistant}],xaxis:{categories:d.labels},colors:[colors[0],colors[1]],stroke:{curve:'smooth'},dataLabels:{enabled:false}}):showEmpty(act);
      const delivery=d.delivery||{}; donut(document.querySelector('#userDeliveryMix'),['متن','وویس','استیکر','خودجوش'],[delivery.text||0,delivery.voice||0,delivery.sticker||0,delivery.proactive||0]);
    }).catch(()=>{showEmpty(document.querySelector('#userActivityTrend'),'خطا در دریافت داده واقعی فعالیت.');showEmpty(document.querySelector('#userDeliveryMix'),'خطا در دریافت داده واقعی ترکیب ارسال.');});
  }

  function initLive(){
    const page=document.querySelector('[data-live-dashboard]'); if(!page)return;
    const feed=document.querySelector('#liveFeed'), err=document.querySelector('#liveError'); let paused=false, auto=true, latest=0, timer=null; const seen=new Set(), msgs=[];
    const val=id=>document.querySelector(id)?.value.trim();
    const params=(after=false)=>{const p=new URLSearchParams({limit:val('#liveLimit')||'100'}); if(after&&latest)p.set('after_id',latest); if(val('#liveUserFilter'))p.set('user_id',val('#liveUserFilter')); const tg=val('#liveTelegramFilter'); if(tg&&/^\d+$/.test(tg))p.set('telegram_id',tg); if(val('#liveRoleFilter'))p.set('role',val('#liveRoleFilter')); const q=[!(/^\d+$/.test(tg||''))?tg:'',val('#liveSearchFilter')].filter(Boolean).join(' '); if(q)p.set('q',q); return p.toString();};
    const escape=s=>(s??'').toString().replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    const card=m=>`<article class="live-message msg-${escape(m.role)} is-new" data-id="${m.id}"><div class="live-message-head"><b>#${m.id}</b><span>${new Date(m.created_at).toLocaleString('fa-IR')}</span><span class="admin-badge">${escape(m.role)}</span></div><p>${escape(m.content)}</p><div class="live-message-meta">کاربر #${m.user_id||'—'} · تلگرام ${m.telegram_id||'—'} · ${escape(m.display_name||'—')} · احساس: ${escape(m.emotion||'—')} · <a href="/admin/users/${m.user_id}">پروفایل</a></div></article>`;
    function stats(){document.querySelector('#liveTotal').textContent=msgs.length;document.querySelector('#liveUsers').textContent=new Set(msgs.map(m=>m.user_id).filter(Boolean)).size;document.querySelector('#liveLatest').textContent=latest||'—';}
    function select(m){document.querySelector('#liveDetail').innerHTML=`<h4>${escape(m.display_name||'کاربر')}</h4><p class="subtle">#${m.user_id} · ${m.telegram_id||'—'}</p><p class="live-full">${escape(m.content)}</p><a class="admin-button" href="/admin/users/${m.user_id}">مشاهده پروفایل</a>`;}
    function draw(newOnes=[]){if(!msgs.length){showEmpty(feed,'پیامی با این فیلترها یافت نشد.');return;} feed.innerHTML=msgs.map(card).join(''); feed.querySelectorAll('.live-message').forEach(el=>el.addEventListener('click',()=>select(msgs.find(m=>m.id==el.dataset.id)))); setTimeout(()=>feed.querySelectorAll('.is-new').forEach(e=>e.classList.remove('is-new')),1600); if(auto&&newOnes.length)feed.scrollTop=0; stats();}
    function load(after=false){if(paused&&after)return; fetchJSON(`/admin/api/live/messages?${params(after)}`).then(d=>{err.classList.add('hidden'); const incoming=d.messages||[]; incoming.forEach(m=>{if(!seen.has(m.id)){seen.add(m.id); msgs.unshift(m); latest=Math.max(latest,m.id);}}); msgs.sort((a,b)=>b.id-a.id); msgs.splice(Number(val('#liveLimit')||100)); draw(incoming);}).catch(()=>{err.textContent='خطا در دریافت پیام‌های زنده.';err.classList.remove('hidden');});}
    ['#liveUserFilter','#liveTelegramFilter','#liveRoleFilter','#liveSearchFilter','#liveLimit'].forEach(id=>document.querySelector(id)?.addEventListener('input',()=>{seen.clear();msgs.length=0;latest=0;load(false);}));
    document.querySelector('#livePauseBtn').onclick=()=>{paused=!paused;document.querySelector('#livePauseBtn').textContent=paused?'ادامه بروزرسانی':'توقف بروزرسانی';document.querySelector('#liveStatusText').textContent=paused?'متوقف':'در حال بروزرسانی';};
    document.querySelector('#liveAutoScrollBtn').onclick=()=>{auto=!auto;document.querySelector('#liveAutoScrollBtn').textContent=`اسکرول خودکار: ${auto?'روشن':'خاموش'}`;};
    document.querySelector('#liveClearBtn').onclick=()=>{['#liveUserFilter','#liveTelegramFilter','#liveRoleFilter','#liveSearchFilter'].forEach(id=>document.querySelector(id).value='');seen.clear();msgs.length=0;latest=0;load(false);};
    load(false); timer=setInterval(()=>load(true),2500); window.addEventListener('beforeunload',()=>clearInterval(timer));
  }

  document.addEventListener('DOMContentLoaded',()=>{document.querySelectorAll('.copy-raw').forEach(btn=>btn.addEventListener('click',()=>{const el=document.querySelector(btn.dataset.copyTarget); if(el&&navigator.clipboard)navigator.clipboard.writeText(el.innerText||'');})); initAnalytics(); initUserCharts(); initLive();});
})();
