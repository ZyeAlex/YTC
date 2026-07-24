/**
 * Cron 选择器（标准 5 段：分 时 日 月 周）
 * 支持：每 N 分钟 / 指定分钟（每小时） / 每 N 小时 / 指定小时
 */
(function (global) {
  const MINUTE = { min: 0, max: 59 };
  const HOUR = { min: 0, max: 23 };

  function clamp(n, bound) {
    return Math.min(bound.max, Math.max(bound.min, Number.isFinite(+n) ? +n : 0));
  }

  function clampMin(n) {
    return clamp(n, MINUTE);
  }

  function clampHour(n) {
    return clamp(n, HOUR);
  }

  function emptyState() {
    return { mode: "none" };
  }

  function defaultState() {
    return { mode: "step", start: 0, step: 20 };
  }

  function parseField(val, bound) {
    const v = (val || "*").trim();
    if (v === "*") return { mode: "every" };
    if (v.includes("-") && !v.includes("/")) {
      const [a, b] = v.split("-");
      return { mode: "range", start: clamp(+a, bound), end: clamp(+b, bound) };
    }
    if (v.includes("/")) {
      const [a, b] = v.split("/");
      const start = a === "*" ? 0 : clamp(+a, bound);
      return { mode: "step", start, step: Math.max(1, +b || 1) };
    }
    if (v.includes(",")) {
      return {
        mode: "specific",
        values: v.split(",").map((x) => clamp(+x, bound)).filter((x) => !Number.isNaN(x)),
      };
    }
    if (/^\d+$/.test(v)) return { mode: "specific", values: [clamp(+v, bound)] };
    return { mode: "every" };
  }

  function serializeField(state, bound) {
    switch (state.mode) {
      case "every":
        return "*";
      case "range":
        return `${clamp(state.start, bound)}-${clamp(state.end, bound)}`;
      case "step": {
        const start = clamp(state.start ?? 0, bound);
        const step = Math.max(1, +(state.step || 1));
        return start === 0 ? `*/${step}` : `${start}/${step}`;
      }
      case "specific": {
        const vals = [...new Set((state.values || []).map((x) => clamp(x, bound)))].sort(
          (a, b) => a - b,
        );
        return vals.length ? vals.join(",") : "0";
      }
      default:
        return "*";
    }
  }

  function parseCron(expr, fallback) {
    const parts = (expr || fallback || "0,20,40 * * * *").trim().split(/\s+/);
    while (parts.length < 5) parts.push("*");
    const mins = parseField(parts[0], MINUTE);
    const hrs = parseField(parts[1], HOUR);
    const restStar = parts.slice(2).every((p) => p === "*");

    // 小时非通配 → 按小时调度（固定到某一分钟）
    if (restStar && hrs.mode !== "every") {
      let minute = 0;
      if (mins.mode === "specific" && mins.values?.length) minute = mins.values[0];
      else if (mins.mode === "step") minute = mins.start || 0;
      else if (/^\d+$/.test(String(parts[0]))) minute = +parts[0];

      if (hrs.mode === "step") {
        return {
          mode: "hour_step",
          minute: clampMin(minute),
          start: hrs.start ?? 0,
          step: Math.max(1, hrs.step || 2),
        };
      }
      if (hrs.mode === "specific" || hrs.mode === "range") {
        let values = hrs.values || [];
        if (hrs.mode === "range") {
          values = [];
          const a = Math.min(hrs.start ?? 0, hrs.end ?? 23);
          const b = Math.max(hrs.start ?? 0, hrs.end ?? 23);
          for (let h = a; h <= b; h++) values.push(h);
        }
        return {
          mode: "hour_specific",
          minute: clampMin(minute),
          values: values.length ? values.map(clampHour) : [0],
        };
      }
    }

    // 每小时内的分钟调度
    if (mins.mode === "step") {
      return { mode: "step", start: mins.start ?? 0, step: Math.max(1, mins.step || 20) };
    }
    if (mins.mode === "specific") {
      return { mode: "specific", values: mins.values?.length ? mins.values : [0] };
    }
    if (mins.mode === "range") {
      return { mode: "range", start: mins.start ?? 0, end: mins.end ?? 59 };
    }
    if (mins.mode === "every") return { mode: "every" };
    return defaultState();
  }

  function serializeCron(state) {
    switch (state.mode) {
      case "every":
        return "* * * * *";
      case "range":
        return `${serializeField(state, MINUTE)} * * * *`;
      case "step":
        return `${serializeField(state, MINUTE)} * * * *`;
      case "specific":
        return `${serializeField(state, MINUTE)} * * * *`;
      case "hour_step": {
        const minute = clampMin(state.minute ?? 0);
        const hourState = {
          mode: "step",
          start: clampHour(state.start ?? 0),
          step: Math.max(1, Math.min(23, +(state.step || 2))),
        };
        return `${minute} ${serializeField(hourState, HOUR)} * * *`;
      }
      case "hour_specific": {
        const minute = clampMin(state.minute ?? 0);
        const hourState = {
          mode: "specific",
          values: (state.values || [0]).map(clampHour),
        };
        return `${minute} ${serializeField(hourState, HOUR)} * * *`;
      }
      default:
        return "0 * * * *";
    }
  }

  function describeCronLocal(expr) {
    const parts = (expr || "").trim().split(/\s+/);
    if (parts.length !== 5) return expr || "";
    const [mins, hrs, dom, mon, dow] = parts;
    if (!(dom === "*" && mon === "*" && dow === "*")) return expr;

    // 按小时
    if (hrs !== "*") {
      const minuteLabel = /^\d+$/.test(mins)
        ? `:${String(+mins).padStart(2, "0")}`
        : `分 ${mins}`;
      if (hrs.startsWith("*/")) return `每 ${hrs.slice(2)} 小时的 ${minuteLabel}`;
      if (hrs.includes("/")) {
        const [start, step] = hrs.split("/");
        return start === "0" || start === "*"
          ? `每 ${step} 小时的 ${minuteLabel}`
          : `从 ${start} 时起每 ${step} 小时的 ${minuteLabel}`;
      }
      if (hrs.includes(",") || /^\d+$/.test(hrs)) {
        const hours = hrs
          .split(",")
          .map(Number)
          .sort((a, b) => a - b)
          .map((h) => `${String(h).padStart(2, "0")}${/^\d+$/.test(mins) ? minuteLabel : ""}`)
          .join("、");
        if (/^\d+$/.test(mins)) return `每天 ${hours}`;
        return `在 ${hrs} 时的 ${minuteLabel}`;
      }
      return `${hrs} 时 ${minuteLabel}`;
    }

    // 按分钟（每小时）
    if (mins === "*") return "每分钟";
    if (mins.startsWith("*/")) return `每 ${mins.slice(2)} 分钟`;
    if (mins.includes("/")) {
      const [start, step] = mins.split("/");
      return start === "0" || start === "*"
        ? `每 ${step} 分钟`
        : `从 ${start} 分起，每 ${step} 分钟`;
    }
    if (mins.includes(",")) {
      const times = mins
        .split(",")
        .map(Number)
        .sort((a, b) => a - b)
        .map((m) => `:${String(m).padStart(2, "0")}`)
        .join("、");
      return `每小时 ${times}`;
    }
    if (/^\d+$/.test(mins)) return `每小时 :${String(+mins).padStart(2, "0")}`;
    return mins;
  }

  function validateCronLocal(expr) {
    const parts = (expr || "").trim().split(/\s+/);
    return parts.length === 5 && parts.every(Boolean);
  }

  class CronPicker {
    constructor(container, options = {}) {
      this.container = typeof container === "string" ? document.querySelector(container) : container;
      this.onChange = options.onChange || (() => {});
      this.allowEmpty = !!options.allowEmpty;
      this.modes = options.modes || ["step", "specific", "hour_step", "hour_specific"];
      this.panelEl = null;
      this.footEl = null;
      this.exprInput = null;
      this.descEl = null;
      this.isEmpty = false;
      this._radioName = `cron-${Math.random().toString(36).slice(2, 8)}`;

      const initial = (options.value || "").trim();
      if (this.allowEmpty && !initial) {
        this.isEmpty = true;
        this.state = emptyState();
      } else {
        this.state = this._normalizeMode(parseCron(initial));
      }

      this._render();
      if (this.isEmpty) {
        this._applyEmptyView(false);
      } else {
        this._syncFromState(false);
      }
    }

    getValue() {
      return this.isEmpty ? "" : this.exprInput.value.trim();
    }

    setValue(expr) {
      if (this.allowEmpty && !(expr || "").trim()) {
        this.isEmpty = true;
        this.state = emptyState();
        this._applyEmptyView(false);
        return;
      }
      this.isEmpty = false;
      this.state = this._normalizeMode(parseCron(expr));
      this._syncFromState(false);
      this._renderPanel();
    }

    _applyEmptyView(emit = true) {
      this.exprInput.value = "";
      this.exprInput.classList.remove("invalid");
      this.descEl.textContent = "未设置（不配置则不会定时执行）";
      this.descEl.classList.remove("error");
      this._renderPanel();
      if (emit) this.onChange("");
    }

    _render() {
      this.container.innerHTML = "";
      this.container.classList.add("cron-picker");

      this.panelEl = document.createElement("div");
      this.panelEl.className = "cron-picker-panel";

      this.footEl = document.createElement("div");
      this.footEl.className = "cron-picker-foot";
      this.exprInput = document.createElement("input");
      this.exprInput.className = "cron-picker-expr";
      this.exprInput.type = "text";
      this.exprInput.spellcheck = false;
      this.exprInput.placeholder = "0 */2 * * *";
      this.exprInput.addEventListener("change", () => this._applyExprInput());
      this.exprInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") this._applyExprInput();
      });
      this.descEl = document.createElement("p");
      this.descEl.className = "cron-picker-desc";
      this.footEl.append(this.exprInput, this.descEl);

      this.container.append(this.panelEl, this.footEl);
      this._renderPanel();
    }

    _normalizeMode(state) {
      if (this.modes.includes(state.mode)) return state;
      if (state.mode === "range") {
        const start = state.start ?? 0;
        const end = state.end ?? 59;
        const step = Math.max(1, end - start || 20);
        if (this.modes.includes("step")) return { mode: "step", start, step };
      }
      if (state.mode === "specific" && state.values?.length && this.modes.includes("specific")) {
        return { mode: "specific", values: [...state.values] };
      }
      if (state.mode === "hour_step" && this.modes.includes("hour_step")) return state;
      if (state.mode === "hour_specific" && this.modes.includes("hour_specific")) return state;
      if (this.modes.includes("step")) return defaultState();
      if (this.modes.includes("hour_step")) {
        return { mode: "hour_step", minute: 0, start: 0, step: 2 };
      }
      return defaultState();
    }

    _ensureModeDefaults(mode) {
      if (mode === "every") return { mode: "every" };
      if (mode === "range") return { mode: "range", start: 0, end: 1 };
      if (mode === "step") return { mode: "step", start: 0, step: 20 };
      if (mode === "specific") return { mode: "specific", values: [0, 20, 40] };
      if (mode === "hour_step") return { mode: "hour_step", minute: 0, start: 0, step: 2 };
      if (mode === "hour_specific") {
        return { mode: "hour_specific", minute: 0, values: [9, 15, 21] };
      }
      return defaultState();
    }

    _renderPanel() {
      const state = this.state;
      this.panelEl.innerHTML = "";

      if (this.modes.includes("every")) {
        this._addOption(state, "every", "每分", null);
      }
      if (this.modes.includes("range")) {
        this._addOption(state, "range", null, (wrap) => {
          wrap.innerHTML =
            '从 <input type="number" class="cron-picker-num" data-k="start" min="0" max="59" /> 到 <input type="number" class="cron-picker-num" data-k="end" min="0" max="59" /> 分';
        });
      }
      if (this.modes.includes("step")) {
        this._addOption(state, "step", null, (wrap) => {
          wrap.innerHTML =
            '从 <input type="number" class="cron-picker-num" data-k="start" min="0" max="59" /> 分开始，每 <input type="number" class="cron-picker-num" data-k="step" min="1" max="59" /> 分';
        });
      }
      if (this.modes.includes("specific")) {
        this._addOption(state, "specific", "指定分钟（每小时）", (wrap) => {
          wrap.appendChild(this._buildMinuteGrid(state));
        });
      }
      if (this.modes.includes("hour_step")) {
        this._addOption(state, "hour_step", null, (wrap) => {
          wrap.innerHTML =
            '每 <input type="number" class="cron-picker-num" data-k="step" min="1" max="23" /> 小时，在 <input type="number" class="cron-picker-num" data-k="minute" min="0" max="59" /> 分' +
            '（从 <input type="number" class="cron-picker-num" data-k="start" min="0" max="23" /> 时起）';
        });
      }
      if (this.modes.includes("hour_specific")) {
        this._addOption(state, "hour_specific", "指定小时", (wrap) => {
          const row = document.createElement("div");
          row.className = "cron-picker-inline";
          row.innerHTML =
            '每天在 <input type="number" class="cron-picker-num" data-k="minute" min="0" max="59" /> 分发送：';
          wrap.appendChild(row);
          wrap.appendChild(this._buildHourGrid(state));
        });
      }
    }

    _buildMinuteGrid(state) {
      const grid = document.createElement("div");
      grid.className = "cron-picker-grid";
      for (let i = 0; i <= 59; i++) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "cron-picker-chip";
        chip.textContent = String(i).padStart(2, "0");
        chip.dataset.v = String(i);
        chip.addEventListener("click", () => {
          this.isEmpty = false;
          if (state.mode !== "specific") {
            Object.assign(state, this._ensureModeDefaults("specific"));
          }
          state.mode = "specific";
          if (!state.values?.length) state.values = [];
          this._toggleSpecific(state, i, 0);
          this._syncFromState();
          this._renderPanel();
        });
        grid.appendChild(chip);
      }
      return grid;
    }

    _buildHourGrid(state) {
      const grid = document.createElement("div");
      grid.className = "cron-picker-grid cron-picker-grid-hours";
      for (let i = 0; i <= 23; i++) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "cron-picker-chip";
        chip.textContent = String(i).padStart(2, "0");
        chip.dataset.v = String(i);
        chip.addEventListener("click", () => {
          this.isEmpty = false;
          if (state.mode !== "hour_specific") {
            Object.assign(state, this._ensureModeDefaults("hour_specific"));
          }
          state.mode = "hour_specific";
          if (!state.values?.length) state.values = [];
          this._toggleSpecific(state, i, 0);
          this._syncFromState();
          this._renderPanel();
        });
        grid.appendChild(chip);
      }
      return grid;
    }

    _addOption(state, mode, labelText, bodyFn) {
      const row = document.createElement("div");
      row.className = "cron-picker-option";
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = this._radioName;
      radio.value = mode;
      radio.checked = !this.isEmpty && state.mode === mode;

      const body = document.createElement("div");
      body.className = "cron-picker-option-body";

      if (labelText && !bodyFn) {
        const label = document.createElement("label");
        label.append(radio, document.createTextNode(` ${labelText}`));
        body.appendChild(label);
      } else {
        body.appendChild(radio);
        const wrap = document.createElement("span");
        wrap.className = "cron-picker-inline";
        if (bodyFn) bodyFn(wrap);
        else if (labelText) wrap.textContent = labelText;
        body.appendChild(wrap);
      }

      radio.addEventListener("change", () => {
        if (!radio.checked) return;
        this.isEmpty = false;
        const next = this._ensureModeDefaults(mode);
        // 切换模式时尽量保留分钟
        if (
          (mode === "hour_step" || mode === "hour_specific") &&
          (state.mode === "step" || state.mode === "specific" || state.mode === "hour_step" || state.mode === "hour_specific")
        ) {
          if (state.mode === "specific" && state.values?.length) next.minute = state.values[0];
          else if (typeof state.minute === "number") next.minute = state.minute;
          else if (typeof state.start === "number" && state.mode === "step") next.minute = state.start;
        }
        Object.keys(state).forEach((k) => delete state[k]);
        Object.assign(state, next);
        this._syncFromState();
        this._renderPanel();
      });

      row.appendChild(body);
      this.panelEl.appendChild(row);

      body.querySelectorAll(".cron-picker-num").forEach((input) => {
        const k = input.dataset.k;
        if (k === "start") input.value = state.start ?? 0;
        if (k === "end") input.value = state.end ?? 59;
        if (k === "step") input.value = state.step ?? (mode.startsWith("hour_") ? 2 : 20);
        if (k === "minute") input.value = state.minute ?? 0;
        input.addEventListener("input", () => {
          if (state.mode !== mode) {
            this.isEmpty = false;
            Object.keys(state).forEach((key) => delete state[key]);
            Object.assign(state, this._ensureModeDefaults(mode));
            radio.checked = true;
          }
          state[k] = +input.value;
          this._syncFromState();
        });
      });

      body.querySelectorAll(".cron-picker-chip").forEach((chip) => {
        const v = +chip.dataset.v;
        const selected =
          !this.isEmpty &&
          ((mode === "specific" && state.mode === "specific" && state.values?.includes(v)) ||
            (mode === "hour_specific" &&
              state.mode === "hour_specific" &&
              state.values?.includes(v)));
        chip.classList.toggle("selected", !!selected);
      });
    }

    _toggleSpecific(state, value, emptyFallback) {
      if (!state.values) state.values = [];
      const idx = state.values.indexOf(value);
      if (idx >= 0) state.values.splice(idx, 1);
      else state.values.push(value);
      if (!state.values.length) {
        if (this.allowEmpty) {
          this.isEmpty = true;
          this.state = emptyState();
          this._applyEmptyView();
          return;
        }
        state.values = [emptyFallback];
      }
    }

    _syncFromState(emit = true) {
      if (this.isEmpty) return;
      const expr = serializeCron(this.state);
      const ok = validateCronLocal(expr);
      this.exprInput.value = expr;
      this.exprInput.classList.toggle("invalid", !ok);
      this.descEl.textContent = ok ? describeCronLocal(expr) : "Cron 格式有误";
      this.descEl.classList.toggle("error", !ok);
      if (emit && ok) this.onChange(expr);
    }

    _applyExprInput() {
      let expr = this.exprInput.value.trim();
      if (this.allowEmpty && !expr) {
        this.isEmpty = true;
        this.state = emptyState();
        this._applyEmptyView();
        return;
      }
      const parts = expr.split(/\s+/);
      if (parts.length === 1) expr = `${parts[0]} * * * *`;
      if (!validateCronLocal(expr)) {
        this.exprInput.classList.add("invalid");
        this.descEl.textContent = "Cron 格式有误";
        this.descEl.classList.add("error");
        return;
      }
      this.isEmpty = false;
      this.state = this._normalizeMode(parseCron(expr));
      this._renderPanel();
      this.exprInput.value = serializeCron(this.state);
      this.descEl.textContent = describeCronLocal(this.exprInput.value);
      this.descEl.classList.remove("error");
      this.onChange(this.exprInput.value);
    }
  }

  global.CronPicker = CronPicker;
  global.describeCronLocal = describeCronLocal;
})(window);
