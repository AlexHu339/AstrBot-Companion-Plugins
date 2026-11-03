const $ = id => document.getElementById(id);
const money = value => `¥${Number(value || 0).toFixed(6)}`;
const quantity = value => Number(value || 0).toLocaleString("zh-CN", {maximumFractionDigits:2});
const date = value => value ? new Date(value * 1000).toLocaleString("zh-CN") : "待核对 · 尚未填写准确日期";
const modes = {trial:"免费试用",package:"已购资源包",paid:"按量付费",unknown:"计费状态待确认"};
function el(tag, text, cls) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n; }
function field(label, input) {const n=el("label",label);n.append(input);return n;}
function input(type, value) { const n=el("input");n.type=type;n.value=value ?? "";return n; }

export class SpeechPanel {
  constructor(request, error) {this.request=request;this.error=error;}
  render(data) {
    const root=$("speech-services");root.replaceChildren();
    for(const service of data?.services||[]){const card=this.card(service);if(data?.adapters&&data.adapters[service.id]===false)card.append(el("p","统计适配器尚未加载：请重载相应语音／唱歌插件，当前不能保证捕获新用量。","error"));root.append(card);}
    const wallet=data?.wallet||{};
    $("volc-wallet").textContent=wallet.state==="estimated"?money(wallet.amount_cny):"待校准";
    $("volc-wallet-note").textContent=wallet.state==="estimated"?`本机估算 · 校准于 ${date(wallet.at)}${wallet.pending?` · ${wallet.pending} 笔待核对`:""}`:"声音复刻与唱歌共用一个火山账户钱包；不会各自拥有一笔余额。";
    $("volc-wallet-form").onsubmit=async e=>{e.preventDefault();await this.save({wallet_cny:Number($("volc-wallet-input").value)},$("volc-wallet-save"));};
    const form=$("speech-expense-form");
    form.onsubmit=async e=>{
      e.preventDefault();if(!form.dataset.eventId)form.dataset.eventId=crypto.randomUUID();
      const at=new Date($("speech-expense-at").value+"+08:00").getTime()/1000;
      const ok=await this.save({event_id:form.dataset.eventId,category:$("speech-expense-category").value,amount:Number($("speech-expense-amount").value),at},$("speech-expense-save"));
      if(ok){form.reset();delete form.dataset.eventId;}
    };
  }
  card(service) {
    const s=service.settings||{}, isAudio=service.unit==="seconds", unit=isAudio?"分钟":"字符";
    const n=el("article",undefined,"platform-card speech-card"), head=el("div",undefined,"section-head");
    head.append(el("h2",service.name),el("span",modes[s.mode]||"待确认","badge"));n.append(head);
    const metrics=el("div",undefined,"platform-metrics");
    for(const [key,label] of [["today","今日"],["month","本月"]]) {
      const stats=service[key]||{}, m=el("div");
      m.append(el("span",label+"生成用量"),el("strong",isAudio?`${quantity(stats.seconds)} 秒`:`${quantity(stats.characters)} 字符`),
        el("small",isAudio?"按云端原始时长计费；歌词不是计费字符":`音频时长 ${quantity(stats.seconds)} 秒 · 时长仅供参考`),
        el("b",`${label}费用 ${money(stats.amount_cny)}`),el("small",`${stats.calls||0} 次请求${stats.pending?` · ${stats.pending} 笔费用待核对`:""}${stats.estimated_chars?` · ${stats.estimated_chars} 笔字符为估算`:""}${stats.rejected?` · ${stats.rejected} 次被拒绝`:""}`));
      metrics.append(m);
    }
    const q=el("div");q.append(el("span",s.mode==="trial"?"免费剩余（本机推算）":s.mode==="package"?"资源包剩余（本机推算）":"额度余额"),
      el("strong",service.remaining==null?"待校准":`${s.precision?"约 ":""}${quantity(isAudio?service.available/60:service.available)} ${unit}`),
      el("small",`${s.mode==="trial"?"试用":"额度"}到期：${date(s.expires)}${service.expired?" · 已到期，剩余额度不可用":""}`),
      el("small",s.at?`${s.origin} · ${date(s.at)}${service.quota_uncertain?` · ${service.quota_uncertain} 笔待核对`:""}`:"尚未校准，不能视为免费余额充足"));metrics.append(q);n.append(metrics);
    n.append(el("p","只记录本机接入后的生成；历史语音不完整，已补回有证据的唱歌。用量不等于QQ发送成功数。免费模式不代表已验证平台仍有额度，额度用尽或转正式版后请更新计费方式。","note"));
    const details=el("details",undefined,"balance-calibration");details.append(el("summary","校准剩余额度、到期时间与付费方式"));
    const form=el("form",undefined,"speech-settings-form"), mode=el("select");
    for(const [value,text] of Object.entries(modes)){const o=el("option",text);o.value=value;mode.append(o);}mode.value=s.mode||"trial";
    const remaining=input("number",s.remaining==null?"":isAudio?Number(s.remaining/60).toFixed(4):s.remaining);remaining.min=0;remaining.step="any";
    const expires=input("datetime-local",s.expires?new Date(s.expires*1000+8*3600*1000).toISOString().slice(0,16):"");
    const rate=input("number",Number((s.rate ?? service.rate)*(isAudio?60:10000)).toFixed(6));rate.min=0;rate.step="any";rate.required=true;
    const approximate=input("checkbox");approximate.checked=Boolean(s.precision);
    form.append(field("当前计费方式",mode),field(`官网当前剩余（${unit}${isAudio?"，可填小数":""}）`,remaining),field("准确到期时间（北京时间；不确定就留空）",expires),field(`按量付费单价（元／${isAudio?"分钟":"万字符"}）`,rate));
    if(isAudio)form.append(field("官网仅显示整分钟，余量需标为估算",approximate));
    const button=el("button","保存并校准"), status=el("span",undefined,"note");status.setAttribute("role","status");form.append(button,status);
    form.onsubmit=async e=>{
      e.preventDefault();const settings={mode:mode.value,remaining:remaining.value===""?null:Number(remaining.value)*(isAudio?60:1),expires:expires.value?new Date(expires.value+"+08:00").getTime()/1000:null,rate:Number(rate.value)/(isAudio?60:10000),precision:isAudio&&approximate.checked?60:0,origin:"用户校准"};
      if(await this.save({service:service.id,settings},button))status.textContent="已保存";
    };
    details.append(el("p","填控制台此刻显示的余量，保存后只扣之后的新用量。试用／资源包内消耗不再重复计现金费；超额列待核对，不擅自认定已付费。按量付费从保存后开始核算，不重算之前试用。充值、购买、转正式版、到期和其他软件消费需重新校准。参考单价来自官方，实际合同可改。","note"),form);n.append(details);return n;
  }
  async save(payload, button) {
    button.disabled=true;
    try {await this.request("speech-settings",payload);window.dispatchEvent(new Event("speech-saved"));return true;}
    catch(e){this.error(e);return false;}finally{button.disabled=false;}
  }
}
