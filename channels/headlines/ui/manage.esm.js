/**
 * Mimir Headlines Channel Manager
 * Custom element: <x-headlines-manager channel-id="com.mimir.headlines">
 */
class HeadlinesManager extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._state = {
      loading:         true,
      setupRequired:   false,
      feeds:           [],
      settings:        {},
      apiKeyInput:     '',
      validating:      false,
      showChangeKey:   false,
      editingId:       null,   // null=closed, ''=new, 'uuid'=editing
      form:            this._blankForm(),
      saving:          false,
      previewUrl:      null,
      previewLoading:  false,
      message:         null,
    };
    this._previewTimer = null;
    this._previewLayouts = ['landscape', 'portrait', 'square'];
    this._previewSizes   = { landscape: [800,480], portrait: [480,800], square: [600,600] };
    this._previewLayout  = 'landscape';
  }

  get channelId() { return this.getAttribute('channel-id') || 'com.mimir.headlines'; }
  get apiBase()   { return `/api/channels/${this.channelId}`; }

  connectedCallback() {
    // Add listeners once on the persistent shadowRoot — not inside _render()
    this.shadowRoot.addEventListener('click',  e => this._handleClick(e));
    this.shadowRoot.addEventListener('change', e => this._handleChange(e));
    this.shadowRoot.addEventListener('input',  e => this._handleChange(e));
    this._load();
  }

  _blankForm() {
    return {
      name:         'My Feed',
      category:     'general',
      query:        '',
      country:      'us',
      language:     'en',
      sort_by:      'publishedAt',
      article_index: 0,
      layout:       'auto',
      theme:        'dark',
      body_size:     'md',
      excerpt_field: 'description',
      show_image:   true,
      show_excerpt: true,
      show_author:  true,
      show_source:  true,
      show_time:    true,
    };
  }

  async _load() {
    this._setState({ loading: true });
    try {
      const [manifest, status] = await Promise.all([
        fetch(`${this.apiBase}/manifest`).then(r => r.json()),
        fetch(`${this.apiBase}/status`).then(r => r.json()),
      ]);
      this._setState({
        loading:       false,
        setupRequired: manifest.setup_required,
        feeds:         status.feeds || [],
        settings:      status.settings || {},
      });
    } catch (e) {
      this._setState({ loading: false, message: { type: 'error', text: `Load failed: ${e.message}` } });
    }
  }

  _setState(patch) {
    const prev = this._state;
    this._state = { ...this._state, ...patch };
    this._render();
  }

  // ── Preview ──────────────────────────────────────────────────────────
  _schedulePreview() {
    clearTimeout(this._previewTimer);
    this._previewTimer = setTimeout(() => this._loadPreview(), 700);
  }

  async _loadPreview() {
    if (!this._state.settings.api_key) return;
    const [w, h] = this._previewSizes[this._previewLayout];
    this._setState({ previewLoading: true, previewUrl: null });
    try {
      const r = await fetch(`${this.apiBase}/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config: this._state.form, w, h }),
      });
      if (!r.ok) throw new Error(await r.text());
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      this._setState({ previewLoading: false, previewUrl: url });
    } catch (e) {
      this._setState({ previewLoading: false });
    }
  }

  // ── API calls ────────────────────────────────────────────────────────
  async _validateKey() {
    const key = this._state.apiKeyInput.trim();
    if (!key) return;
    this._setState({ validating: true, message: null });
    try {
      const r = await fetch(`${this.apiBase}/validate-key`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: key }),
      });
      const data = await r.json();
      if (data.valid) {
        this._setState({ validating: false, showChangeKey: false, apiKeyInput: '' });
        this._load();
      } else {
        this._setState({ validating: false, message: { type: 'error', text: data.error || 'Invalid key' } });
      }
    } catch (e) {
      this._setState({ validating: false, message: { type: 'error', text: e.message } });
    }
  }

  async _saveFeed() {
    const { editingId, form } = this._state;
    const isNew = editingId === '';
    const url = isNew
      ? `${this.apiBase}/subchannels`
      : `${this.apiBase}/subchannels/${editingId}`;
    this._setState({ saving: true });
    try {
      const r = await fetch(url, {
        method: isNew ? 'POST' : 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      if (!r.ok) throw new Error(await r.text());
      this._setState({ saving: false, editingId: null, message: { type: 'success', text: isNew ? 'Feed created.' : 'Feed saved.' } });
      this._load();
    } catch (e) {
      this._setState({ saving: false, message: { type: 'error', text: e.message } });
    }
  }

  async _deleteFeed(id) {
    if (!confirm('Delete this feed?')) return;
    try {
      await fetch(`${this.apiBase}/subchannels/${id}`, { method: 'DELETE' });
      this._load();
    } catch (e) {
      this._setState({ message: { type: 'error', text: e.message } });
    }
  }

  async _openEdit(id) {
    if (id === '') {
      // New feed
      this._setState({ editingId: '', form: this._blankForm(), previewUrl: null });
      this._schedulePreview();
      return;
    }
    try {
      const r = await fetch(`${this.apiBase}/subchannels/${id}`);
      const data = await r.json();
      this._setState({ editingId: id, form: { ...this._blankForm(), ...data }, previewUrl: null });
      this._schedulePreview();
    } catch (e) {
      this._setState({ message: { type: 'error', text: e.message } });
    }
  }

  // ── Event dispatch ──────────────────────────────────────────────────
  _handleClick(e) {
    const action = e.target.closest('[data-action]')?.dataset.action;
    const id     = e.target.closest('[data-id]')?.dataset.id;
    if (!action) return;

    switch (action) {
      case 'validate-key':    this._validateKey();      break;
      case 'change-key':      this._setState({ showChangeKey: true, apiKeyInput: '' }); break;
      case 'cancel-key-change': this._setState({ showChangeKey: false, apiKeyInput: '' }); break;
      case 'add-feed':        this._openEdit('');       break;
      case 'edit-feed':       this._openEdit(id);       break;
      case 'delete-feed':     this._deleteFeed(id);     break;
      case 'cancel-edit':     this._setState({ editingId: null, previewUrl: null }); break;
      case 'save-feed':       this._saveFeed();         break;
      case 'set-preview-layout':
        this._previewLayout = e.target.closest('[data-layout]').dataset.layout;
        this._loadPreview();
        this._render();
        break;
    }
  }

  _handleChange(e) {
    const field = e.target.dataset.field;
    if (!field) return;
    let value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    if (field === 'article_index') value = parseInt(value, 10) || 0;

    if (field === 'apiKeyInput') {
      this._setState({ apiKeyInput: value });
      return;
    }
    this._setState({ form: { ...this._state.form, [field]: value } });
    this._schedulePreview();
  }

  _esc(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ── Render ──────────────────────────────────────────────────────────
  _render() {
    const s = this._state;
    const root = this.shadowRoot;

    // Preserve focus by data-field (inputs have no stable id across rebuilds)
    const active = root.activeElement;
    const focusField = active?.dataset?.field;
    const focusSel   = active?.selectionStart;
    const focusEnd   = active?.selectionEnd;

    root.innerHTML = `<style>${this._css()}</style>${this._html()}`;

    if (focusField) {
      const el = root.querySelector(`[data-field="${focusField}"]`);
      if (el) {
        el.focus();
        if (focusSel !== null && focusSel !== undefined && el.setSelectionRange) {
          try { el.setSelectionRange(focusSel, focusEnd); } catch (_) {}
        }
      }
    }
  }

  _html() {
    const s = this._state;
    if (s.loading) return `<div class="loading">Loading…</div>`;
    if (s.editingId !== null) return this._editPanel();
    return this._listPanel();
  }

  _listPanel() {
    const s = this._state;
    return `
      <div class="panel">
        ${this._messageBar()}
        ${s.setupRequired || s.showChangeKey ? this._keySetupPanel() : this._keyDisplayPanel()}
        ${!s.setupRequired ? `
          <div class="section-header">
            <span>Configured Feeds</span>
            <button class="btn btn-primary" data-action="add-feed">+ Add Feed</button>
          </div>
          ${s.feeds.length === 0
            ? `<div class="empty-state">No feeds yet. Add one to get started.</div>`
            : s.feeds.map(f => this._feedCard(f)).join('')}
        ` : ''}
      </div>`;
  }

  _keySetupPanel() {
    const s = this._state;
    const isChange = s.showChangeKey && !s.setupRequired;
    return `
      <div class="key-panel">
        <div class="key-header">
          <strong>${isChange ? 'Change API Key' : 'NewsAPI Key Required'}</strong>
        </div>
        ${!isChange ? `
        <p class="key-help">
          Get a free API key at <strong>newsapi.org</strong> — free tier supports
          100 requests/day, more than enough for personal display use.
        </p>` : ''}
        <div class="key-row">
          <input id="api-key-input" class="form-input" type="password"
            placeholder="Paste your NewsAPI key…"
            value="${this._esc(s.apiKeyInput)}"
            data-field="apiKeyInput">
          <button class="btn btn-primary" data-action="validate-key"
            ${s.validating ? 'disabled' : ''}>
            ${s.validating ? 'Verifying…' : 'Verify & Save'}
          </button>
          ${isChange ? `<button class="btn btn-ghost" data-action="cancel-key-change">Cancel</button>` : ''}
        </div>
      </div>`;
  }

  _keyDisplayPanel() {
    const s = this._state;
    const key = s.settings?.api_key || '';
    return `
      <div class="key-display">
        <span class="key-mask">${this._esc(key)}</span>
        <button class="btn btn-ghost btn-sm" data-action="change-key">Change Key</button>
      </div>`;
  }

  _feedCard(f) {
    const desc = f.query
      ? `"${this._esc(f.query)}"`
      : this._esc(f.category.charAt(0).toUpperCase() + f.category.slice(1));
    return `
      <div class="feed-card">
        <div class="feed-info">
          <div class="feed-name">${this._esc(f.name)}</div>
          <div class="feed-meta">${desc} · ${this._esc(f.country.toUpperCase())} · ${this._esc(f.layout)} · ${this._esc(f.theme)}</div>
        </div>
        <div class="feed-actions">
          <button class="btn btn-ghost btn-sm" data-action="edit-feed" data-id="${f.id}">Edit</button>
          <button class="btn btn-danger btn-sm" data-action="delete-feed" data-id="${f.id}">Delete</button>
        </div>
      </div>`;
  }

  _editPanel() {
    const s = this._state;
    const f = s.form;
    const isNew = s.editingId === '';
    const [pw, ph] = this._previewSizes[this._previewLayout];

    const opt = (val, label, cur) =>
      `<option value="${val}" ${cur === val ? 'selected' : ''}>${label}</option>`;

    const tog = (field, label) => `
      <label class="toggle-row">
        <input type="checkbox" data-field="${field}" ${f[field] ? 'checked' : ''}>
        <span>${label}</span>
      </label>`;

    const layoutBtn = (lyt) => `
      <button class="layout-btn ${this._previewLayout === lyt ? 'active' : ''}"
        data-action="set-preview-layout" data-layout="${lyt}">
        ${lyt}
      </button>`;

    return `
      <div class="edit-panel">
        <div class="edit-header">
          <h2>${isNew ? 'New Feed' : 'Edit Feed'}</h2>
          ${this._messageBar()}
        </div>
        <div class="edit-body">

          <!-- ── LEFT: FORM ── -->
          <div class="form-col">

            <div class="field-group">
              <label class="field-label">Feed Name</label>
              <input class="form-input" data-field="name" value="${this._esc(f.name)}" placeholder="e.g. Tech News">
            </div>

            <div class="field-row">
              <div class="field-group">
                <label class="field-label">Category</label>
                <select class="form-select" data-field="category">
                  ${opt('general',       'General',       f.category)}
                  ${opt('business',      'Business',      f.category)}
                  ${opt('entertainment', 'Entertainment', f.category)}
                  ${opt('health',        'Health',        f.category)}
                  ${opt('science',       'Science',       f.category)}
                  ${opt('sports',        'Sports',        f.category)}
                  ${opt('technology',    'Technology',    f.category)}
                </select>
              </div>
              <div class="field-group">
                <label class="field-label">Country</label>
                <select class="form-select" data-field="country">
                  ${opt('us', 'United States', f.country)}
                  ${opt('gb', 'United Kingdom', f.country)}
                  ${opt('au', 'Australia',      f.country)}
                  ${opt('ca', 'Canada',         f.country)}
                  ${opt('de', 'Germany',        f.country)}
                  ${opt('fr', 'France',         f.country)}
                  ${opt('jp', 'Japan',          f.country)}
                  ${opt('in', 'India',          f.country)}
                  ${opt('br', 'Brazil',         f.country)}
                </select>
              </div>
            </div>

            <div class="field-group">
              <label class="field-label">
                Keyword Search
                <span class="field-hint">(optional — overrides category &amp; country)</span>
              </label>
              <input class="form-input" data-field="query"
                value="${this._esc(f.query)}" placeholder="e.g. climate change, AI, space">
            </div>

            <div class="field-row">
              <div class="field-group">
                <label class="field-label">Sort By</label>
                <select class="form-select" data-field="sort_by">
                  ${opt('publishedAt', 'Newest First',  f.sort_by)}
                  ${opt('popularity',  'Most Popular',  f.sort_by)}
                  ${opt('relevancy',   'Most Relevant', f.sort_by)}
                </select>
              </div>
              <div class="field-group">
                <label class="field-label">
                  Article # to Show
                  <span class="field-hint">(0 = top)</span>
                </label>
                <input class="form-input" type="number" min="0" max="9" step="1"
                  data-field="article_index" value="${f.article_index}">
              </div>
            </div>

            <div class="divider"></div>

            <div class="field-row">
              <div class="field-group">
                <label class="field-label">Layout</label>
                <select class="form-select" data-field="layout">
                  ${opt('auto',      'Auto-detect', f.layout)}
                  ${opt('landscape', 'Landscape',   f.layout)}
                  ${opt('portrait',  'Portrait',    f.layout)}
                  ${opt('square',    'Square',      f.layout)}
                </select>
              </div>
              <div class="field-group">
                <label class="field-label">Theme</label>
                <select class="form-select" data-field="theme">
                  ${opt('dark',     'Dark',                       f.theme)}
                  ${opt('light',    'Light (Newsprint)',           f.theme)}
                  ${opt('hc-dark',  'High Contrast Dark (e-ink)', f.theme)}
                  ${opt('hc-light', 'High Contrast Light (e-ink)',f.theme)}
                </select>
              </div>
            </div>

            <div class="field-row">
              <div class="field-group">
                <label class="field-label">Body Text Size</label>
                <select class="form-select" data-field="body_size">
                  ${opt('sm', 'Small',  f.body_size)}
                  ${opt('md', 'Medium', f.body_size)}
                  ${opt('lg', 'Large',  f.body_size)}
                </select>
              </div>
            </div>

            <div class="divider"></div>
            <div class="field-label" style="margin-bottom:6px">Content</div>
            <div class="field-row">
              <div class="field-group">
                <label class="field-label">Excerpt Source</label>
                <select class="form-select" data-field="excerpt_field">
                  ${opt('description', 'Description (free tier)',    f.excerpt_field)}
                  ${opt('content',     'Full Content (paid tier)', f.excerpt_field)}
                </select>
              </div>
            </div>
            <div class="toggles-grid">
              ${tog('show_image',   'Show Article Image')}
              ${tog('show_excerpt', 'Show Excerpt')}
              ${tog('show_author',  'Show Author')}
              ${tog('show_source',  'Show Source')}
              ${tog('show_time',    'Show Time')}
            </div>

          </div>

          <!-- ── RIGHT: PREVIEW ── -->
          <div class="preview-col">
            <div class="preview-layout-tabs">
              ${layoutBtn('landscape')}
              ${layoutBtn('portrait')}
              ${layoutBtn('square')}
            </div>
            <div class="preview-frame">
              ${s.previewLoading
                ? `<div class="preview-placeholder">Rendering…</div>`
                : s.previewUrl
                  ? `<img class="preview-img" src="${s.previewUrl}" alt="preview">`
                  : `<div class="preview-placeholder">Preview will appear here</div>`}
            </div>
            <div class="preview-size">${pw} × ${ph}</div>
          </div>

        </div>

        <div class="edit-footer">
          <button class="btn btn-ghost" data-action="cancel-edit">Cancel</button>
          <button class="btn btn-primary" data-action="save-feed" ${s.saving ? 'disabled' : ''}>
            ${s.saving ? 'Saving…' : (isNew ? 'Create Feed' : 'Save Changes')}
          </button>
        </div>
      </div>`;
  }

  _messageBar() {
    const m = this._state.message;
    if (!m) return '';
    return `<div class="message message-${m.type}">${this._esc(m.text)}</div>`;
  }

  _css() {
    return `
      :host { display: block; font-family: system-ui, -apple-system, sans-serif; font-size: 14px; }

      * { box-sizing: border-box; }

      .loading { padding: 32px; text-align: center; color: var(--color-text-secondary, #888); }
      .empty-state { padding: 24px 0; text-align: center; color: var(--color-text-secondary, #888); }

      /* Panel */
      .panel { display: flex; flex-direction: column; gap: 16px; padding: 4px 0; }

      /* Message bar */
      .message {
        padding: 10px 14px;
        border-radius: 6px;
        font-size: 0.85rem;
      }
      .message-error   { background: rgba(198,40,40,0.12); color: #e57373; border: 1px solid rgba(198,40,40,0.25); }
      .message-success { background: rgba(0,200,81,0.10);  color: #4caf50; border: 1px solid rgba(0,200,81,0.2);  }

      /* API key area */
      .key-panel { background: var(--color-surface, #1e2428); border: 1px solid var(--color-border, #2a3035); border-radius: 8px; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
      .key-header { font-weight: 600; }
      .key-help   { font-size: 0.82rem; color: var(--color-text-secondary, #888); line-height: 1.5; }
      .key-row    { display: flex; gap: 8px; }
      .key-display { display: flex; align-items: center; gap: 12px; padding: 10px 14px; background: var(--color-surface, #1e2428); border: 1px solid var(--color-border, #2a3035); border-radius: 8px; }
      .key-mask { font-family: monospace; color: var(--color-text-secondary, #888); flex: 1; }

      /* Section header */
      .section-header { display: flex; align-items: center; justify-content: space-between; }
      .section-header span { font-weight: 600; font-size: 0.9rem; color: var(--color-text-secondary, #888); text-transform: uppercase; letter-spacing: 0.06em; }

      /* Feed cards */
      .feed-card { display: flex; align-items: center; gap: 12px; padding: 12px 14px; background: var(--color-surface, #1e2428); border: 1px solid var(--color-border, #2a3035); border-radius: 8px; }
      .feed-info { flex: 1; min-width: 0; }
      .feed-name { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .feed-meta { font-size: 0.78rem; color: var(--color-text-secondary, #888); margin-top: 2px; text-transform: capitalize; }
      .feed-actions { display: flex; gap: 6px; flex-shrink: 0; }

      /* Edit panel */
      .edit-panel { display: flex; flex-direction: column; height: 100%; gap: 0; }
      .edit-header { padding-bottom: 12px; border-bottom: 1px solid var(--color-border, #2a3035); margin-bottom: 16px; }
      .edit-header h2 { font-size: 1.1rem; font-weight: 700; margin-bottom: 8px; }
      .edit-body { display: flex; gap: 20px; flex: 1; min-height: 0; overflow: auto; }
      .edit-footer { display: flex; justify-content: flex-end; gap: 8px; padding-top: 16px; border-top: 1px solid var(--color-border, #2a3035); margin-top: 16px; }

      /* Form */
      .form-col { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 14px; }
      .field-group { display: flex; flex-direction: column; gap: 5px; }
      .field-row { display: flex; gap: 12px; }
      .field-row .field-group { flex: 1; }
      .field-label { font-size: 0.78rem; font-weight: 600; color: var(--color-text-secondary, #888); text-transform: uppercase; letter-spacing: 0.06em; }
      .field-hint  { font-weight: 400; text-transform: none; letter-spacing: 0; color: var(--color-text-tertiary, #666); }
      .form-input, .form-select {
        background: var(--color-background, #111518);
        border: 1px solid var(--color-border, #2a3035);
        color: var(--color-text, #e0e0e0);
        border-radius: 6px;
        padding: 7px 10px;
        font-size: 0.85rem;
        width: 100%;
      }
      .form-input:focus, .form-select:focus { outline: 2px solid var(--color-accent, #00C851); outline-offset: -1px; }
      .divider { border: none; border-top: 1px solid var(--color-border, #2a3035); }

      /* Toggles */
      .toggles-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 16px; }
      .toggle-row { display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 0.85rem; }
      .toggle-row input[type=checkbox] { width: 15px; height: 15px; accent-color: var(--color-accent, #00C851); cursor: pointer; }

      /* Preview */
      .preview-col { width: 340px; flex-shrink: 0; display: flex; flex-direction: column; gap: 8px; }
      .preview-layout-tabs { display: flex; gap: 4px; }
      .layout-btn { flex: 1; padding: 5px 8px; font-size: 0.72rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; background: var(--color-surface, #1e2428); border: 1px solid var(--color-border, #2a3035); color: var(--color-text-secondary, #888); border-radius: 5px; cursor: pointer; }
      .layout-btn.active { background: var(--color-accent, #00C851); border-color: var(--color-accent, #00C851); color: #000; }
      .preview-frame { background: #000; border: 1px solid var(--color-border, #2a3035); border-radius: 6px; overflow: hidden; display: flex; align-items: center; justify-content: center; min-height: 200px; position: relative; }
      .preview-img { width: 100%; height: auto; display: block; }
      .preview-placeholder { color: var(--color-text-secondary, #555); font-size: 0.8rem; padding: 32px; text-align: center; }
      .preview-size { font-size: 0.68rem; color: var(--color-text-tertiary, #555); text-align: center; letter-spacing: 0.05em; }

      /* Buttons */
      .btn { padding: 7px 14px; border-radius: 6px; font-size: 0.83rem; font-weight: 600; cursor: pointer; border: 1px solid transparent; transition: opacity .15s; }
      .btn:disabled { opacity: .5; cursor: default; }
      .btn-primary { background: var(--color-accent, #00C851); color: #000; }
      .btn-primary:hover:not(:disabled) { opacity: .85; }
      .btn-danger  { background: rgba(198,40,40,0.15); color: #e57373; border-color: rgba(198,40,40,0.3); }
      .btn-danger:hover:not(:disabled) { background: rgba(198,40,40,0.25); }
      .btn-ghost   { background: transparent; color: var(--color-text, #e0e0e0); border-color: var(--color-border, #2a3035); }
      .btn-ghost:hover:not(:disabled) { background: var(--color-surface, #1e2428); }
      .btn-sm { padding: 4px 10px; font-size: 0.76rem; }
    `;
  }
}

customElements.define('x-headlines-manager', HeadlinesManager);
