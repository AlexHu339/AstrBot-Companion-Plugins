import { EmotionPanel } from "./emotion.js";
import { SpeechPanel } from "./speech.js";

const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const $ = id => document.getElementById(id);
const names = {chat:"普通聊天",proactive_chat:"主动聊天",memory_extract:"记忆提取",memory_consolidation:"记忆整理",context_compression:"上下文压缩",voice_style:"语音语气分析",emotion_analysis:"情绪判断",unattributed:"未归类调用",historical_unclassified:"安装前记录（未分类）"};
const money = (n,c="CNY") => `${c === "CNY" ? "¥" : c + " "}${Number(n || 0).toFixed(6)}`;
const size = n => n > 1048576 ? `${(n/1048576).toFixed(1)} MB` : `${(n/1024).toFixed(1)} KB`;
const rhythmKeys=["enabled","merge_enabled","quiet_seconds","max_wait_seconds","closure_enabled","closure_cooldown_minutes","proactive_pause_enabled","format_hint_enabled"];
function node(tag,text,className){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function unwrap(r){if(r?.status === "error")throw Error(r.message);return r?.status === "ok" ? r.data : r;}
async function request(path,body){if(!bridge)throw Error("请从 AstrBot 插件 → LivingMemory → 视图 → companion 打开此页。");return unwrap(await (body===undefined ? bridge.apiGet(`companion/${path}`,{}) : bridge.apiPost(`companion/${path}`,body)));}
function showError(e){$("error").hidden=false;$("error").textContent=e.message || String(e);}
const emotionPanel = new EmotionPanel(request, showError);
const speechPanel = new SpeechPanel(request, showError);
let currentData;
function renderCostDetails(data){
  const period=$("cost-period").value === "month" ? "month" : "today", rows=data.cost_details?.[period] || (period === "month" ? (data.month_details||[]) : (data.today||[]));
  const amount=rows.filter(r=>r.currency === "CNY").reduce((s,r)=>s+Number(r.amount||0),0), calls=rows.reduce((s,r)=>s+Number(r.calls||0),0), pending=rows.reduce((s,r)=>s+Number(r.unknown||0),0);
  $("period-summary").textContent=`${period === "month"?"本月":"今日"} · ${calls} 笔请求／账单记录 · 已核算 ${money(amount)}${pending?` · ${pending} 笔待核对`:""} · 已列出全部接入用途，未触发项目也保留`;
  $("costs").replaceChildren();for(const r of rows){const tr=node("tr");const usage=r.speech?`${Number(r.speech.characters||0).toLocaleString("zh-CN")} 字符 / ${Number(r.speech.seconds||0).toLocaleString("zh-CN")} 秒${r.speech.estimated_chars?" · 字符含估算":""}`:r.kind==="llm"?"token（右列）":r.manual?"手工账单":"—";
    for(const v of [r.label||names[r.category]||r.category,r.model||"未记录模型",r.calls,usage,r.input_other??"—",r.input_cached??"—",r.output??"—",!r.calls ? r.kind==="manual"?"未登记":"本期未触发" : r.amount == null ? "待核对" : money(r.amount,r.currency)+(r.unknown ? ` + ${r.unknown} 笔待核对` : "")])tr.append(node("td",v));if(r.note){tr.title=r.note;tr.firstChild.append(node("small",r.note,"cost-description"));}$("costs").append(tr);}
  if(!rows.length){const tr=node("tr");const td=node("td","所选期间尚无已记录请求。");td.colSpan=8;tr.append(td);$("costs").append(tr);}
}
function renderPlatforms(data){
  for(const id of ["deepseek","mimo"]){const platform=(data.platforms||[]).find(p=>p.id === id);for(const period of ["today","month"]){const stats=platform?.[period];$(id+"-"+period).textContent=stats?money(stats.amount_cny):"—";$(id+"-"+period+"-note").textContent=stats?`${stats.calls} 次调用${stats.pending?` · ${stats.pending} 笔待核对`:""}${stats.unconverted?` · ${stats.unconverted} 笔非人民币另列`:""}`:"等待平台统计";}}
  const other=(data.platforms||[]).find(p=>p.id === "other");$("other-platforms").hidden=!other;if(other)$("other-platforms").textContent=`其他／未识别平台：今日 ${money(other.today.amount_cny)}，本月 ${money(other.month.amount_cny)}；已包含在合计中，明细见费用去向。`;
  const b=data.mimo_balance||{};$("mimo-balance").textContent=b.state === "estimated"?money(b.amount_cny):"待校准";$("mimo-balance-note").textContent=b.state === "estimated"?`校准于 ${new Date(b.at*1000).toLocaleString("zh-CN")} · 非实时余额${b.pending?` · ${b.pending} 笔费用待核对`:""}`:"填入官网余额后按本机新增费用推算";
}
function render(data){
  currentData=data;
  $("error").hidden=true;
  const init=data.initialization||{}, importNames={pending:"等待导入",running:"正在导入",complete:"导入完成",partial:"原文已保留，部分摘要待整理",failed:"导入失败，请检查插件日志"};
  $("initialization").textContent=`记忆组件：${init.ready ? "已就绪" : init.failed ? "启动失败" : "正在启动"} · 旧记录：${importNames[init.history_import]||"状态未知"}`;
  const rows=data.today || [], known=rows.filter(r=>r.currency === "CNY"), amount=known.reduce((s,r)=>s+Number(r.amount||0),0), unknown=rows.reduce((s,r)=>s+Number(r.unknown||0),0);
  $("today").textContent=money(amount);$("unknown").textContent=unknown ? `${unknown} 笔待核对，未计入金额` : "已区分缓存和普通输入";
  $("month").textContent=money((data.month || []).filter(r=>r.currency === "CNY").reduce((s,r)=>s+Number(r.amount||0),0));
  const monthPending=(data.month||[]).reduce((s,r)=>s+Number(r.unknown||0),0);$("month-unknown").textContent=monthPending?`${monthPending} 笔待核对，未计入金额`:"仅含本机观测到的调用";
  const budget=Number(data.daily_budget_cny||0);$("budget").value=budget;$("remaining").textContent=budget>0 ? money(Math.max(0,budget-amount)) : "未设置";
  $("budget-hint").textContent=unknown ? "有待核对请求，剩余额度仅供参考" : "0 表示只统计，不限制";
  renderPlatforms(data);renderCostDetails(data);speechPanel.render(data.speech);
  $("coverage-included").textContent=data.billing_coverage?.included||"统计已观测到的模型调用。";$("coverage-excluded").textContent=data.billing_coverage?.excluded||"以平台账单为准。";
  $("billing-note").textContent=data.billing_note;
  $("started").textContent=data.started_at ? `首笔记录 ${new Date(data.started_at*1000).toLocaleString("zh-CN")}` : "从启用后开始记录";
  const disk=data.storage||{}, total=Object.values(disk).reduce((s,v)=>s+v,0), labels={memory:"记忆正文",indexes:"检索索引",sources_and_ledger:"原文与费用账本",backups:"轮换备份",other:"其他数据"};
  $("storage").replaceChildren();for(const [key,n] of Object.entries(disk)){const row=node("div",undefined,"bar-row"),title=node("div",undefined,"bar-title"),bar=node("div",undefined,"bar"),fill=node("i");title.append(node("span",labels[key]||key),node("span",size(n)));fill.style.width=`${total ? n/total*100 : 0}%`;bar.append(fill);row.append(title,bar);$("storage").append(row);}
  $("root").textContent=data.data_root;$("counts").textContent=`文字原文 ${data.counts.archive_messages} 条 · 有效事实与话题 ${data.counts.active_facets} 条 · 总占用 ${size(total)}`;
  $("limits").replaceChildren();const limitNames={history_tokens:"近期历史预算",history_rounds:"近期完整轮次上限",facts_tokens:"关键事实预算",continuity_tokens:"连续状态预算",purpose_tokens:"可选话头预算",recall_tokens:"相关召回预算"};for(const [key,value] of Object.entries(data.limits||{})){const row=node("div",undefined,"limit");row.append(node("span",limitNames[key]||key),node("b",`${value??"—"}${key.endsWith("tokens")?" tokens（估算）":" 轮"}`));$("limits").append(row);}
  $("traces").replaceChildren();for(const t of data.traces||[]){const detail=node("details"),summary=node("summary",`${new Date(t.at*1000).toLocaleString("zh-CN")} · ${t.proactive ? "主动聊天" : "普通聊天"} · ${t.chat_rhythm?.action === "natural_close" ? "自然收尾（未调用模型）" : t.chat_rhythm?.action === "proactive_deferred" ? "主动消息延后（未调用模型）" : t.query_gate === "greeting_skipped" ? "问候跳过检索" : `${t.chat_rhythm?.action === "merged" ? `合并 ${t.chat_rhythm.message_count} 条 · ` : ""}选中 ${(t.selected||[]).length} 条`}`);detail.append(summary,node("pre",JSON.stringify(t,null,2)));$("traces").append(detail);}if(!(data.traces||[]).length)$("traces").append(node("p","正常聊天后将在这里显示选择记录。","note"));
  const resources=data.resources||{}; $("resources").textContent=`AstrBot 进程内存 ${resources.astrbot_rss_bytes == null ? "无法读取" : size(resources.astrbot_rss_bytes)}；Ollama ${resources.ollama_state === "ok" ? (resources.ollama_models?.length ? resources.ollama_models.map(m=>`${m.name} 驻留 ${size(m.size)}`).join("，") : "当前无模型驻留") : "暂时无法读取"}。模型占用为进程级数据。`;
  renderBalance(data.balance||{});
emotionPanel.render(data.emotion);
const consolidation=data.memory_consolidation||{}, cc=consolidation.config||{}, cr=consolidation.runtime||{}, result=cr.last_result||{};
$("consolidation-enabled").checked=Boolean(cc.enabled);
$("consolidation-rules").textContent=`${cc.trigger==="daily"?"每日凌晨 3 点随维护任务检查":"随记忆反思检查"}；仅整理 ${cc.min_age_days??7} 天前、重要度低于 ${cc.max_importance??0.5} 的记忆；至少 ${cc.min_memories_per_group??3} 条相近记忆成组，每组最多 ${cc.max_group_size??8} 条，每次最多请模型检查 ${cc.max_groups_per_run??2} 组。`;
$("consolidation-state").textContent=cr.running?"正在整理，原记录会保留。":cr.last_run_at?`最近检查：${new Date(cr.last_run_at*1000).toLocaleString("zh-CN")} · 候选 ${result.candidates||0} 条，合并 ${result.merged||0} 条，归档 ${result.archived||0} 条，失败 ${result.failed||0} 组${result.error?"；本轮失败，请检查日志":""}`:"等待首次检查；没有合适的旧记忆时不会强行合并。";
  const rhythm=data.chat_rhythm||{}, controls=rhythm.settings||{};
  for(const key of rhythmKeys){const field=$("rhythm-"+key);if(field.type === "checkbox")field.checked=Boolean(controls[key]);else field.value=controls[key]??"";}
  updateRhythmControls();
  const stats=rhythm.counters||{};$("rhythm-state").textContent=`${controls.enabled?"已启用":"已关闭"} · 正在合并 ${rhythm.pending_batches||0} 组。本次启动：合并 ${stats.merged_batches||0} 组／${stats.merged_messages||0} 条，自然收尾 ${stats.natural_closures||0} 次，暂缓主动消息 ${stats.proactive_deferred||0} 次。`;
}
function renderBalance(b){if(b.state === "ok"){$("balance").textContent=b.balances.map(x=>money(x.total_balance,x.currency)).join(" / ")||"未返回余额";$("balance-time").textContent=`官方余额 · ${new Date((b.updated_at||b.at)*1000).toLocaleString("zh-CN")}`;}else if(b.state === "refresh_failed"){$("balance").textContent=b.balances?.length?b.balances.map(x=>money(x.total_balance,x.currency)).join(" / "):"查询失败";$("balance-time").textContent=b.updated_at?`刷新失败，显示 ${new Date(b.updated_at*1000).toLocaleString("zh-CN")} 的旧余额`:"余额刷新失败，可稍后重试；不是零余额。";}else if(b.state === "unsupported_provider"){$("balance").textContent="未接入";$("balance-time").textContent="未找到启用的官方 DeepSeek 提供商。";}}
async function refresh(withBalance=true){try{render(await request("status"));if(withBalance)renderBalance(await request("balance",{}));}catch(e){showError(e);}}
$("refresh").onclick=refresh;
$("consolidation-enabled").onchange=async()=>{const field=$("consolidation-enabled");try{field.disabled=true;await request("consolidation-settings",{enabled:field.checked});await refresh(false);}catch(e){field.checked=!field.checked;showError(e);}finally{field.disabled=false;}};
$("consolidation-run").onclick=async()=>{try{$("consolidation-run").disabled=true;await request("consolidation-run",{});await refresh(false);}catch(e){showError(e);}finally{$("consolidation-run").disabled=false;}};
window.addEventListener("speech-saved",()=>refresh(false));
$("cost-period").onchange=()=>{if(currentData)renderCostDetails(currentData);};
$("mimo-anchor-form").onsubmit=async e=>{e.preventDefault();try{const value=$("mimo-anchor").value;if(!value.trim())throw Error("请先填写官网余额");$("mimo-anchor-save").disabled=true;await request("balance-reference",{platform:"mimo",amount_cny:Number(value)});await refresh(false);$("mimo-anchor-saved").textContent="已保存并校准，后续余额为本机估算。";}catch(err){showError(err);}finally{$("mimo-anchor-save").disabled=false;}};
$("memory-manager").onclick=async()=>{try{$("memory-manager").disabled=true;const entry=await request("view-entry",{view:"dashboard"});window.location.assign(entry.url);}catch(e){showError(e);}finally{$("memory-manager").disabled=false;}};
$("balance-refresh").onclick=async()=>{try{$("balance-refresh").disabled=true;renderBalance(await request("balance",{}));}catch(e){showError(e);}finally{$("balance-refresh").disabled=false;}};
$("budget-form").onsubmit=async e=>{e.preventDefault();try{await request("settings",{daily_budget_cny:Number($("budget").value)});await refresh();}catch(err){showError(err);}};
$("rhythm-form").onsubmit=async e=>{e.preventDefault();try{const update={};for(const key of rhythmKeys){const field=$("rhythm-"+key);update[key]=field.type === "checkbox"?field.checked:Number(field.value);}await request("rhythm",update);await refresh();$("rhythm-saved").textContent=" 已保存并生效";}catch(err){showError(err);}};
try{if(bridge)await bridge.ready();await refresh();}catch(e){showError(e);}

function updateRhythmControls(){
  const enabled=$("rhythm-enabled").checked, merge=enabled&&$("rhythm-merge_enabled").checked;
  for(const key of ["merge_enabled","closure_enabled","proactive_pause_enabled","format_hint_enabled"])$("rhythm-"+key).disabled=!enabled;
  $("rhythm-wait-fields").disabled=!merge;
  $("rhythm-close-fields").disabled=!(enabled&&$("rhythm-closure_enabled").checked);
  $("rhythm-wait-note").textContent=!enabled?"聊天节奏已关闭，下方等待时间不生效。":merge?"已开启：只在此处等待，把连续纯文字一起理解。":"已关闭：收到消息就开始处理，不等待下面这两个时间；模型生成和发送仍需要时间。";
}
for(const key of ["enabled","merge_enabled","closure_enabled"])$("rhythm-"+key).addEventListener("change",updateRhythmControls);
