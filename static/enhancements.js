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
  catalogPage=page;$('catalogState').textContent=t('loading');
  const target=$('products');target.setAttribute('aria-busy','true');
  try{
    const d=await api('catalog?q='+encodeURIComponent(q)+'&page='+page+'&category='+encodeURIComponent(catalogCategory));
    target.replaceChildren(...d.products.map(card));
    if(!d.products.length)target.append(el('p',d.syncing?(lang==='kk'?'Каталог жүктелуде. Бірнеше секундтан кейін іздеуді қайталаңыз.':'Каталог загружается. Повторите поиск через несколько секунд.'):t('empty')));
    $('catalogState').textContent=d.products.length+(d.total?' / '+d.total:'')+' · '+t('verified');
    const nav=$('catalogNav');nav.replaceChildren();
    if(page>1)nav.append(button(t('previous'),()=>catalog(q,page-1),'outline'));
    if(d.has_more)nav.append(button(t('more'),()=>catalog(q,page+1),'outline'));
  }catch(e){$('catalogState').textContent=t('error');fail(e);}
  finally{target.removeAttribute('aria-busy');}
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
  else b.append(el('p',t('noCertificate'),'muted'));
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
cart=async function(){await originalCart();const b=$('modalBody');if($('count').textContent==='0')b.prepend(el('p',t('cartEmpty')));b.append(externalLink(t('localCart'),'/cart','cart-state-link'));};
const originalConfirmation=showConfirmation;
showConfirmation=function(d,confirm,id){return originalConfirmation(d,async()=>{const result=await confirm();const note=bubble(t('confirmed'));note.append(el('br'),externalLink(t('localCart'),result.cart_url||'/cart','cart-state-link'));return result;},id);};

send=async function(text,mode='best'){
  if(!text.trim()||$('send').disabled)return;
  openChat();bubble(text,'user');$('message').value='';$('send').disabled=true;
  const progress=bubble(t('checking'));progress.classList.add('progress');
  try{
    const d=await api('query',{text,language:lang,mode});progress.remove();
    bubble(d.message||t('empty'));latestProducts=d.products||[];
    if(!d.action){latestQuery=d.query||text;for(const q of d.clarification_questions||[])bubble(q);}
    for(const p of d.products||[]){$('messages').append(card(p));if(p.reason)bubble(p.reason);}
    if(d.events?.length){const activity=el('details',undefined,'activity-log');activity.append(el('summary',(lang==='kk'?'Тексеру қадамдары':'Ход проверки')+' · '+(d.route||'DIRECT')));const names={'Requirements extracted':['Требования выделены','Талаптар анықталды'],'Catalog searched':['Каталог проверен','Каталог тексерілді'],'Solution generated':['Подборка готова','Таңдау дайын'],'Live stock rechecked':['Остаток перепроверен','Қор қайта тексерілді'],'Local cart updated':['Корзина обновлена','Себет жаңартылды']};for(const event of d.events){const text=names[event]?.[lang==='kk'?1:0]||(lang==='kk'?'Каталог деректері тексерілді':event);activity.append(el('div','✓ '+text));}$('messages').append(activity);}
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
for(const key of ['all','breaker','cable','enclosure']){const b=button(t(key),()=>{catalogCategory=key==='all'?'':key;categories.querySelectorAll('button').forEach(x=>x.classList.toggle('active',x===b));return catalog($('searchText').value);},key==='all'?'active':'outline');b.dataset.extra=key;categories.append(b);}
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
