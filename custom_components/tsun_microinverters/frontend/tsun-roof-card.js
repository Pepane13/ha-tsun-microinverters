/* TSUN roof card: live production of the 6 panels, port of the Homey dashboard widget.
 * Served by the integration and loaded automatically. Configuration:
 *   type: custom:tsun-roof-card
 *   entity: sensor.tsun_mx3000d_power   # power sensor of the inverter (required)
 *   order: 5,4,2,6,1,3                   # panel order on the roof, row by row (optional)
 *   columns: 3                           # panels per row (optional)
 *   panel_max: 500                       # W for a full panel (optional)
 */
const LABELS = {
  en: { today: 'Today', total: 'Total', peak: 'Peak today', grid: 'Grid', inverter: 'Inverter', efficiency: 'Efficiency DC→AC',
    wifi: 'Logger link', updated: 'Updated', of: 'of', expected: 'usual production at this time', dc: 'Panels (DC)',
    forecast: 'Forecast (left / day)', missing: 'Entity not found', derating: 'Thermal derating' },
  fr: { today: "Aujourd'hui", total: 'Total', peak: 'Pic du jour', grid: 'Réseau', inverter: 'Onduleur', efficiency: 'Rendement DC→AC',
    wifi: 'Liaison logger', updated: 'Mis à jour', of: 'de', expected: 'production habituelle à cette heure', dc: 'Panneaux (DC)',
    forecast: 'Prévision (reste / jour)', missing: 'Entité introuvable', derating: 'Bridage thermique' },
};

const CSS = `
  :host { --panel-body: #1f3554; --panel-cell: rgba(255,255,255,.10); --panel-fill: rgba(242,140,40,.88); --panel-off: #6b7280;
          --chip-bg: var(--secondary-background-color, #f4f5f7); }
  ha-card { padding: 16px; }
  .wrap { display: flex; flex-direction: column; gap: 12px; }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .status { display: flex; align-items: center; gap: 6px; font-size: 14px; font-weight: 500; color: var(--primary-text-color); }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--success-color, #2fb344); flex: none; }
  .dot.warn { background: var(--warning-color, #f28c28); } .dot.bad { background: var(--error-color, #e5484d); }
  .dot.off { background: var(--disabled-text-color, #999); }
  .updated { font-size: 13px; color: var(--secondary-text-color); white-space: nowrap; }
  .power { display: flex; align-items: baseline; gap: 6px; }
  .power .val { font-size: 32px; line-height: 40px; font-weight: 700; color: var(--primary-text-color); }
  .power .unit { font-size: 20px; color: var(--secondary-text-color); }
  .gauge { height: 6px; border-radius: 3px; background: var(--chip-bg); overflow: hidden; }
  .gauge > div { height: 100%; background: var(--panel-fill); border-radius: 3px; transition: width .6s; }
  .gauge-label { font-size: 12px; color: var(--secondary-text-color); margin-top: 3px; }
  .chips { display: grid; grid-template-columns: repeat(auto-fill, minmax(118px, 1fr)); gap: 6px; }
  .chip { background: var(--chip-bg); border-radius: 8px; padding: 6px 8px; min-width: 0; cursor: pointer; }
  .chip .k { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .chip .v { font-size: 14px; font-weight: 500; color: var(--primary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .chip .v.warn { color: var(--warning-color, #c76b00); }
  .panels { display: grid; gap: 8px; }
  .panel { position: relative; aspect-ratio: 2 / 3; border-radius: 8px; background: var(--panel-body); overflow: hidden;
           border: 2px solid transparent; color: #fff; cursor: pointer; }
  .panel.off { background: var(--panel-off); } .panel.under { border-color: var(--warning-color, #f28c28); }
  .panel.zero { border-color: var(--error-color, #e5484d); }
  .panel .fill { position: absolute; left: 0; right: 0; bottom: 0; background: var(--panel-fill); transition: height .6s; }
  .panel .cells { position: absolute; inset: 0; background-size: 33.34% 16.67%;
    background-image: linear-gradient(var(--panel-cell) 1px, transparent 1px), linear-gradient(90deg, var(--panel-cell) 1px, transparent 1px); }
  .panel .expected { position: absolute; left: 0; right: 0; height: 0; }
  .panel .expected::before, .panel .expected::after { content: ''; position: absolute; top: -1px; width: 16%; border-top: 3px solid rgba(255,255,255,.95); }
  .panel .expected::before { left: 0; border-radius: 0 2px 2px 0; } .panel .expected::after { right: 0; border-radius: 2px 0 0 2px; }
  .panel .txt { position: absolute; inset: 0; display: flex; flex-direction: column; justify-content: space-between; padding: 6px;
                text-shadow: 0 1px 2px rgba(0,0,0,.55); }
  .panel .name { font-size: 12px; font-weight: 700; display: flex; justify-content: space-between; }
  .panel .w { font-size: 20px; font-weight: 700; text-align: center; line-height: 1.1; }
  .panel .w small { display: block; font-size: 11px; font-weight: 500; opacity: .9; }
  .panel .kwh { font-size: 12px; text-align: center; }
  .legend { font-size: 12px; color: var(--secondary-text-color); display: flex; gap: 12px; flex-wrap: wrap; }
  .legend i { display: inline-block; width: 10px; border-top: 3px solid var(--secondary-text-color); vertical-align: middle; margin-right: 4px; }
  .empty { font-size: 14px; color: var(--secondary-text-color); text-align: center; padding: 24px 0; }
`;

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

class TsunRoofCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) throw new Error('entity (power sensor) is required');
    this._config = config;
    this._order = String(config.order || '1,2,3,4,5,6').split(/[^0-9]+/).map(Number).filter((n) => n >= 1 && n <= 6);
    this._columns = Number(config.columns) || 3;
    this._panelMax = Number(config.panel_max) || 500;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: 'open' });
      this.shadowRoot.innerHTML = `<style>${CSS}</style><ha-card><div class="wrap"></div></ha-card>`;
      this.shadowRoot.addEventListener('click', (ev) => {
        const el = ev.composedPath().find((x) => x.dataset && x.dataset.entity);
        if (el) this._more(el.dataset.entity);
      });
    }
    this._sig = null;
  }

  getCardSize() { return 9; }

  getGridOptions() { return { columns: 12, min_columns: 6, rows: 'auto' }; }

  static getStubConfig(hass) {
    const e = Object.values(hass.entities || {}).find((x) => x.platform === 'tsun_microinverters' && x.translation_key === 'power');
    return { entity: e ? e.entity_id : 'sensor.tsun_mx3000d_power', order: '1,2,3,4,5,6', columns: 3 };
  }

  _more(entityId) {
    this.dispatchEvent(new CustomEvent('hass-more-info', { bubbles: true, composed: true, detail: { entityId } }));
  }

  /** Sibling entities of the power sensor (same device), by translation key. */
  _siblings(hass) {
    const reg = hass.entities || {};
    const me = reg[this._config.entity];
    if (!me || !me.device_id) return {};
    if (this._sibDevice === me.device_id && this._sib) return this._sib;
    const out = {};
    for (const e of Object.values(reg)) {
      if (e.device_id === me.device_id && e.translation_key && !out[e.translation_key]) out[e.translation_key] = e.entity_id;
    }
    this._sibDevice = me.device_id;
    this._sib = out;
    return out;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    const sib = this._siblings(hass);
    const st = (key) => (sib[key] ? hass.states[sib[key]] : undefined);
    const sig = [this._config.entity, ...Object.values(sib)].map((id) => {
      const s = hass.states[id];
      return s ? `${s.state}|${s.last_updated}` : '';
    }).join(';');
    if (sig === this._sig) return;
    this._sig = sig;
    this._render(hass, st, sib);
  }

  _render(hass, st, sib) {
    const L = LABELS[(hass.language || 'en').slice(0, 2)] || LABELS.en;
    const root = this.shadowRoot.querySelector('.wrap');
    const pw = hass.states[this._config.entity];
    if (!pw) {
      root.innerHTML = `<div class="empty">${esc(L.missing)} : ${esc(this._config.entity)}</div>`;
      return;
    }
    const locale = hass.locale && hass.locale.language ? hass.locale.language : hass.language;
    const num = (v, d = 0) => {
      const n = Number(v);
      return v == null || v === '' || Number.isNaN(n) ? '—' : n.toLocaleString(locale, { minimumFractionDigits: d, maximumFractionDigits: d });
    };
    const val = (key) => { const s = st(key); return s && !['unknown', 'unavailable'].includes(s.state) ? s.state : null; };
    const on = (key) => { const s = st(key); return !!s && s.state === 'on'; };
    const a = pw.attributes || {};
    const panels = Array.isArray(a.panels) ? a.panels : [];
    const power = Number(pw.state) || 0;
    const rated = a.rated_power || 3000;
    const pct = Math.min(100, (100 * power) / rated);

    let dot = 'ok';
    if (a.unreachable || on('alarm') || on('communication')) dot = 'bad';
    else if (a.standby) dot = 'off';
    else if (panels.some((p) => p.state === 'under' || p.state === 'zero') || on('derating')) dot = 'warn';

    const latency = val('latency');
    const rate = val('comm_rate');
    const slow = latency != null && Number(latency) > 3000;
    const lowRate = rate != null && Number(rate) < 95;
    const peak = val('peak');
    const peakAt = st('peak') && st('peak').attributes.at;
    const dc = val('dc_power');
    const eff = val('efficiency');
    const chips = [
      [L.today, `${num(val('energy_today'), 2)} kWh`, 'energy_today'],
      [L.total, `${num(val('energy_total'), 1)} kWh`, 'energy_total'],
      [L.peak, peak && Number(peak) > 0 ? `${num(peak)} W · ${esc(peakAt || '')}` : '—', 'peak'],
      [L.grid, `${num(val('voltage'), 1)} V · ${num(val('frequency'), 2)} Hz`, 'voltage'],
      [L.inverter, `${num(val('temperature'))} °C${on('derating') ? ` · ${esc(L.derating)}` : ''}`, 'temperature', on('derating')],
      [L.dc, dc != null && !a.standby ? `${num(dc)} W` : '—', 'dc_power'],
      [L.efficiency, eff != null && !a.standby ? `${num(eff, 1)} %` : '—', 'efficiency'],
      [L.wifi, `${latency != null ? `${num(latency)} ms` : '—'} · ${rate != null ? `${num(rate)} %` : '—'}`, 'comm_rate', slow || lowRate],
    ];
    if (val('forecast_today') != null) {
      chips.splice(2, 0, [L.forecast, `${num(val('forecast_remaining'), 1)} / ${num(val('forecast_today'), 1)} kWh`, 'forecast_today']);
    }

    const byN = Object.fromEntries(panels.map((p) => [p.n, p]));
    const list = (this._order.length ? this._order : [1, 2, 3, 4, 5, 6]).map((n) => byN[n]).filter(Boolean);
    const anyExpected = list.some((p) => p.expected != null);
    const panelsHtml = list.map((p) => {
      const h = Math.max(0, Math.min(100, (100 * (p.power || 0)) / this._panelMax));
      const eh = p.expected != null ? Math.max(0, Math.min(100, (100 * p.expected) / this._panelMax)) : null;
      const flag = p.state === 'zero' ? '⛔' : p.state === 'under' ? '⚠︎' : '';
      return `<div class="panel ${p.state}" data-entity="${esc(p.entity_id || this._config.entity)}">
        <div class="fill" style="height:${p.state === 'off' ? 0 : h}%"></div>
        <div class="cells"></div>
        ${eh != null && p.state !== 'off' ? `<div class="expected" style="bottom:${eh}%"></div>` : ''}
        <div class="txt">
          <div class="name"><span>PV${p.n}</span><span>${flag}</span></div>
          <div class="w">${p.state === 'off' ? '—' : `${num(p.power)} W`}
            ${p.voltage != null ? `<small>${num(p.voltage, 1)} V · ${num(p.current, 2)} A</small>` : ''}</div>
          <div class="kwh">${num(p.energy, 2)} kWh</div>
        </div>
      </div>`;
    }).join('');

    const status = val('status') || '';
    root.innerHTML = `
      <div class="head">
        <div class="status" data-entity="${esc(sib.status || this._config.entity)}"><span class="dot ${dot}"></span>${esc(this._config.title || status)}${this._config.title ? ` · ${esc(status)}` : ''}</div>
        <div class="updated">${a.last_read ? `${esc(L.updated)} ${esc(a.last_read)}` : ''}</div>
      </div>
      <div data-entity="${esc(this._config.entity)}" style="cursor:pointer">
        <div class="power"><span class="val">${num(power)}</span><span class="unit">W</span></div>
        <div class="gauge"><div style="width:${pct}%"></div></div>
        <div class="gauge-label">${num(pct)} % ${esc(L.of)} ${num(rated)} W</div>
      </div>
      <div class="chips">${chips.map(([k, v, key, w]) => `<div class="chip" data-entity="${esc(sib[key] || this._config.entity)}"><div class="k">${esc(k)}</div><div class="v ${w ? 'warn' : ''}">${v}</div></div>`).join('')}</div>
      <div class="panels" style="grid-template-columns: repeat(${this._columns}, 1fr)">${panelsHtml}</div>
      ${anyExpected ? `<div class="legend"><span><i></i>${esc(L.expected)}</span></div>` : ''}`;
  }
}

if (!customElements.get('tsun-roof-card')) {
  customElements.define('tsun-roof-card', TsunRoofCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: 'tsun-roof-card', name: 'TSUN roof', description: 'Live production of the TSUN micro-inverter panels', preview: false });
}
