/** Render isolated companion affect; all text uses DOM text nodes. */
export class EmotionPanel {
  constructor(request, showError) {
    this.request = request;
    this.showError = showError;
    this.selected = null;
    this.generation = 0;
    const $ = id => document.getElementById(id);
    $("emotion-refresh").onclick = () => this.refresh();
    $("emotion-namespace").onchange = () => {
      this.selected = JSON.parse($("emotion-namespace").value);
      this.refresh();
    };
    $("emotion-form").onsubmit = async event => {
      event.preventDefault();
      const update = {};
      for (const key of this.keys || []) {
        const field = $("emotion-" + key);
        update[key] = field.type === "checkbox" ? field.checked : field.type === "number" ? Number(field.value) : field.value;
      }
      try {
        $("emotion-save").disabled = true;
        await this.request("emotion-settings", update);
        await this.refresh();
        $("emotion-saved").textContent = " 已保存并生效";
      } catch (error) { this.showError(error); }
      finally { $("emotion-save").disabled = false; }
    };
    $("emotion-reset").onclick = async () => {
      if (!this.selected) return;
      try {
        $("emotion-reset").disabled = true;
        this.render(await this.request("emotion-reset", this.selected));
      } catch (error) { this.showError(error); }
      finally { $("emotion-reset").disabled = !this.selected; }
    };
  }

  async refresh() {
    const generation = ++this.generation;
    try {
      const data = await this.request("emotion-status", this.selected || {});
      if (generation === this.generation) this.render(data);
    } catch (error) { this.showError(error); }
  }

  render(data) {
    if (!data) return;
    if (this.selected && JSON.stringify(this.selected) !== JSON.stringify(data.selected) && (data.namespaces || []).some(item => JSON.stringify(item) === JSON.stringify(this.selected))) {
      this.refresh();
      return;
    }
    this.selected = data.selected;
    const $ = id => document.getElementById(id);
    const node = (tag, text, className) => {
      const element = document.createElement(tag);
      if (text !== undefined) element.textContent = text;
      if (className) element.className = className;
      return element;
    };
    const controls = data.settings || {};
    this.keys = Object.keys(controls);
    for (const [key, value] of Object.entries(controls)) {
      const field = $("emotion-" + key);
      if (!field) continue;
      if (field.type === "checkbox") field.checked = Boolean(value);
      else field.value = value;
    }
    $("emotion-namespace").replaceChildren();
    for (const item of data.namespaces || []) {
      const option = node("option", item.scope + " · " + item.persona);
      option.value = JSON.stringify(item);
      option.selected = option.value === JSON.stringify(data.selected);
      $("emotion-namespace").append(option);
    }
    $("emotion-namespace").disabled = !(data.namespaces || []).length;
    $("emotion-reset").disabled = !data.selected;
    const state = data.state || {}, mood = Number(state.mood ?? controls.baseline ?? 70);
    $("emotion-score").textContent = mood.toFixed(1);
    $("emotion-summary").textContent = !controls.enabled ? "情绪判断已关闭" : !state.last_assessed ? "等待正常聊天后的判断" : mood >= 80 ? "心情愉快" : mood >= 62 ? "平稳亲近" : mood >= 42 ? "有些牵挂" : "需要慢慢回稳";
    const states = { waiting: "等待满足判断条件", pending: "后台判断中", applied: "已更新心情", unchanged: "没有明显变化", low_confidence: "判断把握不足，未改变心情", failed: "判断失败，保留原心情", budget: "预算已达上限，暂缓判断", cancelled: "判断已取消", disabled: "已关闭", queue_full: "等待队列已满，本轮暂缓" };
    const weak = state.last_assessed && Math.max(0,...Object.values(state.emotions||{})) < 0.1;
    $("emotion-state-note").textContent = `${weak ? "当前情绪已基本回稳；" : ""}上次判断：${states[state.last_status] || "尚无判断"} · 待处理 ${data.pending || 0} 次 · 本轮情绪背景 ${data.context_estimated_tokens || 0} tokens（估算）`;
    $("emotion-reason").textContent = state.last_assessed ? `上次有效原因：${state.reason} · 模型自评可信度 ${Math.round(Number(state.confidence || 0) * 100)}% · ${new Date(state.last_assessed * 1000).toLocaleString("zh-CN")}` : "正常私聊产生记录后，会显示变化原因。这里的数值是角色表达状态。";
    $("emotion-bars").replaceChildren();
    for (const [key, label] of Object.entries(data.labels || {})) {
      const value = Math.max(0, Math.min(100, Number(state.emotions?.[key] || 0)));
      const row = node("div", undefined, "bar-row"), title = node("div", undefined, "bar-title"), bar = node("div", undefined, "bar"), fill = node("i");
      title.append(node("span", label), node("span", value > 0 && value < 0.05 ? "<0.1" : value.toFixed(1)));
      fill.style.width = value + "%";
      bar.append(fill); row.append(title, bar); $("emotion-bars").append(row);
    }
    $("emotion-history").replaceChildren();
    for (const record of data.history || []) {
      const details = node("details"), delta = Number(record.after || 0) - Number(record.before || 0);
      details.append(node("summary", `${new Date(record.at * 1000).toLocaleString("zh-CN")} · ${states[record.status] || record.status} · ${Number(record.before).toFixed(1)} → ${Number(record.after).toFixed(1)} (${delta >= 0 ? "+" : ""}${delta.toFixed(1)})`));
      details.append(node("p", record.reason, "note"));
      if (record.evidence) details.append(node("p", "依据的用户原话：" + record.evidence, "note"));
      const values = Object.entries(record.emotions || {}).map(([key, value]) => `${data.labels[key] || key} ${Number(value).toFixed(1)}`).join(" · ");
      details.append(node("p", values, "note"));
      $("emotion-history").append(details);
    }
    if (!(data.history || []).length) $("emotion-history").append(node("p", "尚无情绪判断记录。查看或刷新此页不会调用聊天模型。", "note"));
  }
}
