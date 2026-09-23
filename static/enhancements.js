'use strict';
// Extend the team's storefront while preserving its server-owned confirmation flow.
Object.assign(words, {
  call:['Позвонить','Қоңырау шалу'], write:['Написать в WhatsApp','WhatsApp-қа жазу'],
  mail:['Написать письмо','Хат жазу'], contactSource:['Контакты на сайте EKT ↗','EKT сайтындағы байланыс ↗'],
  localCart:['Открыть мою корзину ↗','Менің себетімді ашу ↗'], cartEmpty:['Корзина пока пуста','Себет әзірге бос'],
  unavailable:['Нет в наличии','Қорда жоқ'], unknown:['Не указано','Көрсетілмеген'],
  verified:['Проверено по API EKT','EKT API арқылы тексерілді'], sourceName:['Названия и характеристики сохранены как в каталоге EKT.','Атаулар мен сипаттамалар EKT каталогындағыдай сақталған.'],
  certificate:['Сертификат ↗','Сертификат ↗'], noCertificate:['Ссылка на сертификат отсутствует в API','API-де сертификат сілтемесі жоқ'],
  alternative:['Аналог','Баламасы'], clarify:['Нужна проверка','Тексеру қажет'], discuss:['Обсудить с ИИ','AI-мен талқылау'],
  cheapest:['Дешевле','Арзанырақ'], available:['В наличии','Қорда бар'], best:['Лучшее совпадение','Ең сәйкес'],
  more:['Следующие товары →','Келесі тауарлар →'], previous:['← Назад','← Артқа'],
  all:['Все товары','Барлық тауарлар'], breaker:['Автоматы','Автоматтар'], cable:['Кабель','Кабель'], enclosure:['Щиты и шкафы','Қалқандар мен шкафтар'],
  checking:['Проверяю запрос, каталог и остатки…','Сұрауды, каталогты және қорды тексеріп жатырмын…'],
  confirmed:['Товары добавлены.','Тауарлар қосылды.'], close:['Закрыть','Жабу'], send:['Отправить','Жіберу'],
  selection:['Результат подбора','Таңдау нәтижесі'], recognized:['Распознано','Танылды'],
});

let catalogRequest=0;
let catalogPage=1, catalogCategory='', latestQuery='', latestProducts=[];
const originalTranslate=translate;
translate=function(){
  originalTranslate();
  $('closeChat').setAttribute('aria-label',t('close'));
  $('closeModal').setAttribute('aria-label',t('close'));
  $('send').setAttribute('aria-label',t('send'));
  $('message').setAttribute('aria-label',t('message'));
  $('searchText').setAttribute('aria-label',t('search'));
  document.querySelectorAll('[data-extra]').forEach(n=>n.textContent=t(n.dataset.extra));
  if($('chatLanguage'))$('chatLanguage').value=lang;
  $('mode').textContent=t(/ДЕМО/.test($('mode').textContent)?'demo':'live');
};

card=function(p){
  const n=el('article',undefined,'product'),visual=el('div','⚡','product-image');
  const url=safeURL(p.image);
  if(url){visual.textContent='';const image=el('img');image.src=url;image.alt='';image.loading='lazy';image.onerror=()=>{image.remove();visual.textContent='⚡';};visual.append(image);}
  n.append(visual,el('span',p.article||String(p.id),'muted'),el('h3',p.name));
  if(p.demo_logistics){const x=p.demo_logistics.destination;n.append(el('p','DEMO · '+x.city+' · '+x.days_min+'–'+x.days_max+(lang==='kk'?' күн':' дн.'),'demo-shipping'));const locations=el('details');locations.append(el('summary',lang==='kk'?'Виртуалды қор':'Виртуальные остатки'));for(const l of p.demo_logistics.locations)locations.append(el('div',l.city+': '+l.quantity));n.append(locations);}
  if(p.certificate)n.append(el('span',lang==='kk'?'Сертификат бар':'Есть сертификат','tag'));
  if(p.alternative_for)n.append(el('span',t('alternative')+' · '+p.alternative_for,'tag'));
  const stock=el('div',p.quantity==null?t('unknown'):p.quantity>0?t('stock')+p.quantity:t('unavailable'),'stock');
  if(!p.quantity)stock.classList.add('out');
  n.append(stock,el('div',money(p.price),'price'));
  if(p.reason&&n.closest?.('.messages'))n.append(el('p',p.reason,'muted'));
  if(p.warnings?.length)n.append(el('span',t('clarify'),'tag warning'));
  n.append(button(t('details'),()=>details(p.id),'outline'));
  return n;
};

catalog=async function(q='',page=1){
  if(new URLSearchParams(location.search).get('embed')==='1')return;
  const request=++catalogRequest;
  catalogPage=page;$('catalogState').textContent=t('loading');
  const target=$('products');target.setAttribute('aria-busy','true');
  try{
    const d=await api('catalog?q='+encodeURIComponent(q)+'&page='+page+'&category='+encodeURIComponent(catalogCategory));
    if(request!==catalogRequest)return;
    target.replaceChildren(...d.products.map(card));
    if(!d.products.length)target.append(el('p',d.syncing?(lang==='kk'?'Каталог жүктелуде. Бірнеше секундтан кейін іздеуді қайталаңыз.':'Каталог загружается. Повторите поиск через несколько секунд.'):t('empty')));
    $('catalogState').textContent=d.products.length+(d.total?' / '+d.total:'')+' · '+t('verified');
    const nav=$('catalogNav');nav.replaceChildren();
    if(page>1)nav.append(button(t('previous'),()=>catalog(q,page-1),'outline'));
    if(d.has_more)nav.append(button(t('more'),()=>catalog(q,page+1),'outline'));
  }catch(e){$('catalogState').textContent=t('error');fail(e);}
  finally{if(request===catalogRequest)target.removeAttribute('aria-busy');}
};

function externalLink(label,url,cls='contact-link'){
  const a=el('a',label,cls);a.href=url;
  if(url.startsWith('https:')){a.target='_blank';a.rel='noopener noreferrer';}
  return a;
}

handoff=async function(){
  const d=await api('manager?language='+lang),b=modal(t('manager'));
  b.append(el('p',d.message,'receipt'),el('p',d.hours,'muted'));
  const links=el('div',undefined,'contact-links');
  links.append(externalLink(t('call')+' · '+d.phone,d.phone_url),externalLink(t('write'),d.whatsapp_url),externalLink(t('mail')+' · '+d.email,d.email_url));
  b.append(links,externalLink(t('contactSource'),d.source,'muted'));
  b.append(el('p',lang==='kk'?'Таңдалған тауарларды менеджерге өзіңіз жібере аласыз. Чат тарихы автоматты түрде берілмейді.':'Вы можете самостоятельно сообщить менеджеру артикулы выбранных товаров. История чата автоматически не передаётся.','notice'));
  if(latestProducts.length)b.append(el('p',latestProducts.slice(0,5).map(p=>(p.article||p.id)+' — '+p.name).join('\n'),'receipt'));
};

details=async function(id){
  const p=await api('products/'+id),b=modal(p.name);
  b.append(el('p','ID '+p.id+' · '+(p.article||''),'muted'),el('h2',money(p.price)),el('p',p.quantity==null?t('unknown'):t('stock')+p.quantity));
  const labels={brand:['Бренд','Бренд'],poles:['Полюса','Полюстер'],current:['Номинальный ток','Номиналды ток'],voltage:['Напряжение','Кернеу'],breaking_capacity:['Отключающая способность','Ажырату қабілеті'],base:['Цоколь','Цоколь'],power:['Мощность','Қуат'],color_temperature:['Цветовая температура','Түс температурасы'],luminous_flux:['Световой поток','Жарық ағыны'],mounting:['Способ монтажа','Орнату тәсілі'],application:['Область применения','Қолдану саласы']};
  const attrs=el('dl',undefined,'attributes');
  for(const [k,v]of Object.entries(p.attributes||{}))if(v){attrs.append(el('dt',labels[k]?.[lang==='kk'?1:0]||k),el('dd',v));}
  b.append(attrs,el('p',t('sourceName'),'muted'));
  if(p.description){const section=el('details'),summary=el('summary',lang==='kk'?'Каталогтағы сипаттама':'Описание из каталога');section.append(summary,el('p',p.description,'receipt'));b.append(section);}
  if(p.certificate){try{const u=new URL(p.certificate);if(u.protocol==='https:')b.append(externalLink(t('certificate'),u.href));}catch{}}
  else {b.append(el('p',t('noCertificate'),'notice'));b.append(button(lang==='kk'?'Сертификатты менеджерден сұрау':'Запросить сертификат у менеджера',()=>{doneModal();return handoff();},'outline'));}
  for(const w of p.warnings||[])b.append(el('p',lang==='kk'?t('compatibility')+' '+w:w,'warning'));
  const qty=field(b,t('qty'),'number','1');qty.min='1';qty.max=String(Math.min(p.quantity||10000,10000));qty.step='1';qty.inputMode='numeric';
  const label=el('label',t('store'),'field'),select=el('select');select.append(new Option(t('allStores'),''));
  for(const s of p.stores||[])if(s.quantity>0&&s.id)select.append(new Option(s.name+' · '+s.quantity,s.id));
  label.append(select);b.append(label);
  const link=safeURL(p.url);if(link)b.append(externalLink(t('official'),link,'muted'));
  const add=button(t('add'),async()=>{if(!qty.checkValidity()){qty.reportValidity();return;}const line={product_id:p.id,quantity:Number(qty.value),store_id:select.value?Number(select.value):null};const prepared=await api('cart/prepare',line);doneModal();showConfirmation(prepared,()=>api('cart/confirm',{...line,confirmation_token:prepared.confirmation_token}),id);});
  add.disabled=Boolean(p.warnings?.length||!p.quantity||p.price==null);
  $('modalActions').append(button(t('discuss'),()=>{doneModal();send(String(id)).catch(fail);},'outline'));
  if(add.disabled)$('modalActions').append(button(t('manager'),()=>{doneModal();return handoff();},'outline'));
  $('modalActions').append(add);
};

const originalCart=cart;
words.expensive=['Подороже','Қымбатырақ'];
cross=async function(id){
  const prefs=await api('session');if(prefs.recommendations_enabled===false)return;
  openChat();const n=bubble(lang==='kk'?'Қажет болса, қосымша бұйымдарды каталогтан тексере аламын. Олар автоматты түрде қосылмайды.':'Если интересно, могу проверить дополнения по каталогу. Они не добавятся автоматически; это подбор по назначению, а не статистика чужих покупок.');
  const show=button(lang==='kk'?'Қордағы нұсқаларды көрсету':'Показать варианты в наличии',async()=>{show.disabled=true;try{const d=await api('recommendations/'+id);bubble(d.message);for(const p of d.products||[]){$('messages').append(card(p));bubble(p.reason);}}catch(e){bubble(t('error'));$('messages').append(button(t('manager'),handoff,'outline'));}});
  n.append(show,button(t('stopCross'),async()=>{await api('session/recommendations',{enabled:false});n.remove();},'outline'));
};
cart=async function(){
  const d=await refreshCart(),b=modal(t('cart'));b.append(el('p',t('notice'),'notice'));let total=0;
  if(!d.cart.length)b.append(el('p',t('cartEmpty')));
  for(const [index,p]of d.cart.entries()){
    total+=p.price*p.quantity;
    const row=el('div',undefined,'cart-item'),visual=el('div','⚡','cart-photo'),url=safeURL(p.image);
    if(url){visual.textContent='';const img=el('img');img.src=url;img.alt=p.name;img.onerror=()=>{img.remove();visual.textContent='⚡';};visual.append(img);}
    const info=el('div',undefined,'cart-info');info.append(el('strong',p.name),el('p',(p.article||p.id)+' · '+money(p.price),'muted'),el('p',p.store_name,'muted'),el('strong',money(p.price*p.quantity)));
    const controls=el('div',undefined,'cart-controls'),qty=el('input');qty.type='number';qty.min='1';qty.max='10000';qty.step=String(p.purchase_multiple||1);qty.value=p.quantity;qty.setAttribute('aria-label',t('qty'));
    const edit=async quantity=>{if(quantity!==0&&!qty.checkValidity()){qty.reportValidity();return;}const prepared=await api('cart/prepare-edit',{index,quantity});doneModal();showConfirmation(prepared,()=>api('cart/confirm-edit',{confirmation_token:prepared.confirmation_token}));};
    controls.append(button('−',()=>{qty.value=String(Math.max(1,Number(qty.value)-Number(qty.step)));},'outline'),qty,button('+',()=>{qty.value=String(Math.min(10000,Number(qty.value)+Number(qty.step)));},'outline'),button(t('change'),()=>edit(Number(qty.value))),button(t('remove'),()=>edit(0),'outline'));
    row.append(visual,info,controls);b.append(row);
  }
  b.append(el('h2',t('total')+': '+money(total)),externalLink(t('localCart'),'/cart','cart-state-link'));
};
showConfirmation=function(d,confirm,id){
  const editing=Boolean(d.previous),b=modal(t('preview'));
  if(editing)b.append(el('p',d.previous.name+'\n'+t('qty')+': '+d.previous.quantity+' → '+(d.items?.[0]?.quantity||0),'notice'));
  for(const p of d.items||[])b.append(el('p',p.quantity+' × '+p.name+'\n'+money(p.price)+' · '+p.store_name+'\n'+money(p.line_total),'receipt'));
  if(d.remove)b.append(el('p',t('remove')+': '+d.previous.name));
  b.append(el('h2',(editing?(lang==='kk'?'Позиция сомасы':'Сумма позиции'):t('total'))+': '+money(d.total)),el('p',t('twoMinutes'),'notice'));
  cancelModal=()=>api('cart/cancel',{confirmation_token:d.confirmation_token});
  const go=button(t('confirm'),async()=>{go.disabled=true;try{const result=await confirm();doneModal();await refreshCart();toast(t('saved'));if(editing){await cart();}else{const note=bubble(t('confirmed'));note.append(el('br'),externalLink(t('localCart'),result.cart_url||'/cart','cart-state-link'));if(id)await cross(id);}}catch(e){go.disabled=false;throw e;}});
  $('modalActions').append(button(t('cancel'),async()=>{await closeModal();if(editing)await cart();},'outline'),go);
};

send=async function(text,mode='best'){
  if(!text.trim()||$('send').disabled)return;
  openChat();bubble(text,'user');$('message').value='';$('send').disabled=true;
  const progress=bubble(t('checking'));progress.classList.add('progress');
  try{
    const d=await api('query',{text,language:lang,mode});progress.remove();
    bubble(d.message||t('empty'));latestProducts=d.products||[];
    if(!d.action){latestQuery=d.query||text;for(const q of d.clarification_questions||[])bubble(q);}
    for(const p of d.products||[]){$('messages').append(card(p));if(p.reason)bubble(p.reason);}
    if(d.events?.length){const activity=el('details',undefined,'activity-log');activity.append(el('summary',(lang==='kk'?'Тексеру қадамдары':'Ход проверки')));const names={'Requirements extracted':['Требования выделены','Талаптар анықталды'],'Catalog searched':['Каталог проверен','Каталог тексерілді'],'Solution generated':['Подборка готова','Таңдау дайын'],'Live stock rechecked':['Остаток перепроверен','Қор қайта тексерілді'],'Local cart updated':['Корзина обновлена','Себет жаңартылды']};for(const event of d.events){const text=names[event]?.[lang==='kk'?1:0]||(lang==='kk'?'Каталог деректері тексерілді':event);activity.append(el('div','✓ '+text));}$('messages').append(activity);}
    if(d.handoff_available||d.action==='manager')$('messages').append(button(t('manager'),handoff,'outline'));
    if(d.action==='cart_confirmation'){const n=bubble(t('twoMinutes'));n.append(button(t('confirm'),()=>send(lang==='kk'?'иә, қос':'да, добавь')),button(t('cancel'),()=>send(lang==='kk'?'бас тарту':'отмена'),'outline'));}
    if(d.action==='cart_added'){const n=bubble(t('confirmed'));n.append(el('br'),externalLink(t('localCart'),d.cart_url||'/cart','cart-state-link'));if(d.cart?.length)await cross(d.cart[d.cart.length-1].id);}
    await refreshCart();
  }catch(e){progress.remove();bubble(lang==='kk'?'Деректерді тексеру мүмкін болмады. Кейінірек қайталаңыз немесе менеджерге хабарласыңыз.':'Не удалось проверить данные. Попробуйте позже или свяжитесь с менеджером.');$('messages').append(button(t('manager'),handoff,'outline'));}
  finally{$('send').disabled=false;$('messages').scrollTop=$('messages').scrollHeight;}
};

const originalOpenChat=openChat;
openChat=function(){originalOpenChat();document.body.classList.add('chat-open');};
$('closeChat').onclick=()=>{$('chat').classList.add('hidden');$('launcher').classList.remove('hidden');document.body.classList.remove('chat-open');};
const nav=el('div',undefined,'catalog-nav');nav.id='catalogNav';$('products').after(nav);
const categories=el('nav',undefined,'categories');categories.setAttribute('aria-label','Категории / Санаттар');
words.lamp=['Лампы','Шамдар'];
for(const key of ['all','lamp','breaker','cable','enclosure']){const b=button(t(key),()=>{catalogCategory=key==='all'?'':key;categories.querySelectorAll('button').forEach(x=>{x.classList.toggle('active',x===b);x.classList.toggle('outline',x!==b);x.setAttribute('aria-pressed',String(x===b));});return catalog($('searchText').value);},key==='all'?'active':'outline');b.dataset.extra=key;b.setAttribute('aria-pressed',String(key==='all'));categories.append(b);}
$('products').before(categories);
const modes=el('div',undefined,'chat-modes');
for(const [label,mode]of [['best','best'],['cheapest','cheapest'],['expensive','expensive'],['available','available']]){const b=button(t(label),()=>latestQuery?send(latestQuery,mode):toast(t('greeting')),'outline');b.dataset.extra=label;modes.append(b);}
document.querySelector('.chat-tools').after(modes);
const chatLanguage=el('select');chatLanguage.id='chatLanguage';chatLanguage.setAttribute('aria-label','Язык чата / Чат тілі');chatLanguage.append(new Option('RU','ru'),new Option('ҚАЗ','kk'));
chatLanguage.onchange=async()=>{lang=chatLanguage.value;translate();try{await api('session/language',{language:lang});bubble(t('greeting'));}catch(e){fail(e);}};
$('closeChat').before(chatLanguage);
const contacts=button(t('call'),handoff,'outline header-contact');contacts.dataset.extra='call';$('cart').before(contacts);
const fitViewport=()=>document.documentElement.style.setProperty('--viewport-height',(window.visualViewport?.height||window.innerHeight)+'px');
window.visualViewport?.addEventListener('resize',fitViewport);fitViewport();
if(new URLSearchParams(location.search).get('embed')==='1'){document.body.classList.add('embed-mode');openChat();}
if(location.pathname==='/cart'){const openCurrentCart=async()=>{await api('session');await cart();};openCurrentCart().catch(fail);}
translate();

const logisticsBar=el('div',undefined,'logistics-settings');
const logisticsToggle=check(logisticsBar,lang==='kk'?'DEMO жеткізу — виртуалды деректер':'ДЕМО доставки — виртуальные сроки и остатки');
const deliveryCity=el('select');deliveryCity.setAttribute('aria-label','Город / Қала');for(const city of ['Алматы','Астана','Шымкент','Караганда'])deliveryCity.append(new Option(city,city));logisticsBar.append(deliveryCity);
logisticsBar.append(el('small','Данные EKT и корзина используют реальный остаток. / Себет нақты қорды қолданады.'));
document.querySelector('.chat-modes').after(logisticsBar);
const saveLogistics=async()=>{try{await api('session/logistics',{enabled:logisticsToggle.checked,city:deliveryCity.value});bubble(logisticsToggle.checked?(lang==='kk'?'DEMO: жеткізу мерзімі мен қалалардағы қор виртуалды.':'ДЕМО включено: сроки доставки и остатки по городам виртуальные. Цены и доступное для корзины количество — из EKT.'):(lang==='kk'?'DEMO өшірілді.':'ДЕМО доставки выключено.'));}catch(e){fail(e);}};
logisticsToggle.onchange=saveLogistics;deliveryCity.onchange=saveLogistics;
api('session/logistics').then(d=>{logisticsToggle.checked=d.enabled;deliveryCity.value=d.city;}).catch(fail);
