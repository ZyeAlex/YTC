/**
 * 分钟 Cron 选择器（输出标准 5 段 cron，时/日/月/周固定为 *）
 */
(function (global) {
  const MINUTE = { min: 0, max: 59 };

  function parseMinuteField(val) {
    const v = (val || "*").trim();
    if (v === "*") return { mode: "every" };
    if (v.includes("-")) {
      const [a, b] = v.split("-");
      return { mode: "range", start: clamp(+a), end: clamp(+b) };
    }
    if (v.includes("/")) {
      const [a, b] = v.split("/");
      const start = a === "*" ? 0 : clamp(+a);
      return { mode: "step", start, step: Math.max(1, +b) };
    }
    if (v.includes(",")) {
      return { mode: "specific", values: v.split(",").map((x) => clamp(+x)) };
    }
    if (/^\d+$/.test(v)) return { mode: "specific", values: [clamp(+v)] };
    return { mode: "every" };
  }

  function serializeMinuteField(state) {
    switch (state.mode) {
      case "every":
        return "*";
      case "range":
        return `${state.start}-${state.end}`;
      case "step":
        return state.start === 0 ? `*/${state.step}` : `${state.start}/${state.step}`;
      case "specific":
        return [...new Set(state.values)].sort((a, b) => a - b).join(",");
      default:
        return "*";
    }
  }

  function clamp(n) {
    return Math.min(MINUTE.max, Math.max(MINUTE.min, n || 0));
  }

  function emptyMinuteState() {
    return { mode: "none" };
  }

  function parseCron(expr, fallback) {
    const parts = (expr || fallback || "0,20,40 * * * *").trim().split(/\s+/);
    return parseMinuteField(parts[0]);
  }

  function serializeCron(minuteState) {
    return `${serializeMinuteField(minuteState)} * * * *`;
  }

  function describeCronLocal(expr) {
    const parts = (expr || "").trim().split(/\s+/);
    if (parts.length !== 5) return expr || "";
    const mins = parts[0];
    const hourly = parts.slice(1).every((p) => p === "*");
    if (!hourly) return `每分钟 ${mins}（时/日/月/周非 *，仅分钟生效）`;

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
      this.panelEl = null;
      this.footEl = null;
      this.exprInput = null;
      this.descEl = null;
      this.isEmpty = false;

      const initial = (options.value || "").trim();
      if (this.allowEmpty && !initial) {
        this.isEmpty = true;
        this.minuteState = emptyMinuteState();
      } else {
        this.minuteState = parseCron(initial);
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
        this.minuteState = emptyMinuteState();
        this._applyEmptyView(false);
        return;
      }
      this.isEmpty = false;
      this.minuteState = parseCron(expr);
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
      this.exprInput.placeholder = "0,20,40 * * * *";
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

    _renderPanel() {
      const state = this.minuteState;
      this.panelEl.innerHTML = "";

      this._addOption(state, "every", "每分", null);
      this._addOption(state, "range", null, (wrap) => {
        wrap.innerHTML =
          '从 <input type="number" class="cron-picker-num" data-k="start" min="0" max="59" /> 到 <input type="number" class="cron-picker-num" data-k="end" min="0" max="59" /> 分';
      });
      this._addOption(state, "step", null, (wrap) => {
        wrap.innerHTML =
          '从 <input type="number" class="cron-picker-num" data-k="start" min="0" max="59" /> 分开始，每 <input type="number" class="cron-picker-num" data-k="step" min="1" max="59" /> 分';
      });
      this._addOption(state, "specific", "指定分钟", (wrap) => {
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
            state.mode = "specific";
            if (!state.values?.length) state.values = [];
            this._toggleSpecific(state, i);
            this._syncFromState();
            this._renderPanel();
          });
          grid.appendChild(chip);
        }
        wrap.appendChild(grid);
      });
    }

    _addOption(state, mode, labelText, bodyFn) {
      const row = document.createElement("div");
      row.className = "cron-picker-option";
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = `cron-minute-${Math.random().toString(36).slice(2, 8)}`;
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
        state.mode = mode;
        if (state.mode === "every") state.values = [];
        if (state.mode === "range") {
          state.start = 0;
          state.end = 1;
        }
        if (state.mode === "step") {
          state.start = 0;
          state.step = 20;
        }
        if (state.mode === "specific") {
          state.values = state.values?.length ? state.values : [0];
        }
        this._syncFromState();
        this._renderPanel();
      });

      row.appendChild(body);
      this.panelEl.appendChild(row);

      body.querySelectorAll(".cron-picker-num").forEach((input) => {
        const k = input.dataset.k;
        if (k === "start") input.value = state.start ?? 0;
        if (k === "end") input.value = state.end ?? 59;
        if (k === "step") input.value = state.step ?? 20;
        input.addEventListener("input", () => {
          if (state.mode !== mode) {
            this.isEmpty = false;
            state.mode = mode;
            radio.checked = true;
          }
          state[k] = +input.value;
          this._syncFromState();
        });
      });

      body.querySelectorAll(".cron-picker-chip").forEach((chip) => {
        const v = +chip.dataset.v;
        chip.classList.toggle(
          "selected",
          !this.isEmpty && state.mode === "specific" && state.values?.includes(v),
        );
      });
    }

    _toggleSpecific(state, value) {
      if (!state.values) state.values = [];
      const idx = state.values.indexOf(value);
      if (idx >= 0) state.values.splice(idx, 1);
      else state.values.push(value);
      if (!state.values.length) {
        if (this.allowEmpty) {
          this.isEmpty = true;
          this.minuteState = emptyMinuteState();
          this._applyEmptyView();
          return;
        }
        state.values = [0];
      }
    }

    _syncFromState(emit = true) {
      if (this.isEmpty) return;
      const expr = serializeCron(this.minuteState);
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
        this.minuteState = emptyMinuteState();
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
      this.minuteState = parseCron(expr);
      this._renderPanel();
      this.exprInput.value = serializeCron(this.minuteState);
      this.onChange(this.exprInput.value);
    }
  }

  global.CronPicker = CronPicker;
})(window);
