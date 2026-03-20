// schema.js — Schema dropdown, floating tree browser, label selection
// Extracted from viewer.html as an ES module.

// ── State (module-private, exposed on window via defineProperty) ──

let activeSchema = null;        // null or {name, tree: [...]}
let activeSchemaMode = 'none';  // 'none' | 'ukhab' | 'custom'
let _schemaTargetInput = null;  // if set, selectSchemaLabel fills this input instead

Object.defineProperty(window, 'activeSchema', {
    get: () => activeSchema,
    set: (v) => { activeSchema = v; },
    configurable: true,
});
Object.defineProperty(window, 'activeSchemaMode', {
    get: () => activeSchemaMode,
    set: (v) => { activeSchemaMode = v; },
    configurable: true,
});

// ── Schema Float ──

function openSchemaForCluster(inputEl) {
    if (!activeSchema) return;
    _schemaTargetInput = inputEl;
    openSchemaFloat();
}

function openSchemaFloat() {
    if (!activeSchema) return;
    if (!_schemaTargetInput) _schemaTargetInput = null;
    const panel = document.getElementById('schema-float');
    document.getElementById('schema-float-title').textContent = activeSchema.name || 'Schema';
    document.getElementById('schema-tree-container').innerHTML = renderSchemaTreeHTML(activeSchema.tree, 0);
    document.getElementById('schema-float-search').value = '';
    panel.style.display = 'block';
}

function closeSchemaFloat() {
    _schemaTargetInput = null;
    document.getElementById('schema-float').style.display = 'none';
}

// Init dragging on schema float once DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    const sf = document.getElementById('schema-float');
    const sh = document.getElementById('schema-float-header');
    if (sf && sh) window.makeDraggable(sf, sh);
});

// ── Schema System ──

function toggleSchemaDropdown() {
    const menu = document.getElementById('schema-dropdown-menu');
    menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
}

async function loadSchema(mode) {
    document.getElementById('schema-dropdown-menu').style.display = 'none';
    activeSchemaMode = mode;
    localStorage.setItem('schemaMode', mode);

    if (mode === 'none') {
        activeSchema = null;
        renderSchemaSelector();
        return;
    }

    const builtinSchemas = {
        ukhab: { url: '/schemas/ukhab-v2.json', label: 'UKHab' },
        hotw:  { url: '/schemas/hotw.json',      label: 'HOTW' },
    };

    if (builtinSchemas[mode]) {
        const schema = builtinSchemas[mode];
        try {
            const resp = await fetch(schema.url);
            if (!resp.ok) throw new Error(`Failed to load ${schema.label} schema`);
            activeSchema = await resp.json();
        } catch (e) {
            console.error(`[SCHEMA] Failed to load ${schema.label}:`, e);
            alert(`Failed to load ${schema.label} schema.`);
            activeSchemaMode = 'none';
            activeSchema = null;
        }
    }

    renderSchemaSelector();
}

function loadCustomSchema(file) {
    if (!file) return;
    document.getElementById('schema-dropdown-menu').style.display = 'none';
    const reader = new FileReader();
    reader.onload = function(e) {
        const text = e.target.result;
        try {
            // Try JSON first
            const parsed = JSON.parse(text);
            if (parsed.tree && Array.isArray(parsed.tree)) {
                activeSchema = parsed;
            } else if (Array.isArray(parsed)) {
                activeSchema = { name: file.name.replace(/\.[^.]+$/, ''), tree: parsed };
            } else {
                throw new Error('Invalid schema format');
            }
        } catch (jsonErr) {
            // Fall back to tab-indented text
            activeSchema = parseTabIndentedSchema(text, file.name);
        }
        activeSchemaMode = 'custom';
        localStorage.setItem('schemaMode', 'custom');
        renderSchemaSelector();
    };
    reader.readAsText(file);
}

function parseTabIndentedSchema(text, filename) {
    const lines = text.split('\n').filter(l => l.trim());
    const root = [];
    const stack = [{ children: root, depth: -1 }];

    for (const line of lines) {
        const stripped = line.replace(/\t/g, '    ');
        const indent = stripped.search(/\S/);
        if (indent < 0) continue;
        const depth = Math.floor(indent / 4);
        const content = stripped.trim();

        // Try to extract code and name: "g1a Lowland dry acid grassland"
        const codeMatch = content.match(/^([a-z]\w*)\s+(.+)$/i);
        const node = codeMatch
            ? { code: codeMatch[1].toLowerCase(), name: codeMatch[2], children: [] }
            : { code: '', name: content, children: [] };

        // Pop stack to correct parent level
        while (stack.length > 1 && stack[stack.length - 1].depth >= depth) {
            stack.pop();
        }
        stack[stack.length - 1].children.push(node);
        stack.push({ children: node.children, depth });
    }

    return { name: (filename || 'Custom').replace(/\.[^.]+$/, ''), tree: root };
}

function renderSchemaSelector() {
    // Update top-bar schema button label
    const btn = document.getElementById('schema-dropdown-btn');
    if (btn) {
        btn.innerHTML = (activeSchema && activeSchemaMode !== 'none')
            ? `Schema: ${activeSchema.name} &#9662;`
            : 'Schema &#9662;';
    }
    // Re-render cluster rows so S buttons appear/disappear
    const segLabels = window.segLabels;
    if (segLabels && segLabels.length > 0) window.showSegmentationPanel();

    const container = document.getElementById('manual-label-selector');
    if (!container) return;

    const currentManualLabel = window.currentManualLabel;

    if (!activeSchema || activeSchemaMode === 'none') {
        // Restore freetext mode
        container.style.flexDirection = '';
        container.style.alignItems = '';
        closeSchemaFloat();
        container.innerHTML = `
            <span style="font-weight: 600; color: #333; font-size: 12px; white-space: nowrap;">Current label:</span>
            <div id="manual-label-swatch" title="Click to choose colour" style="width: 18px; height: 18px; border-radius: 3px; border: 2px solid #666; background: ${currentManualLabel ? currentManualLabel.color : '#3cb44b'}; flex-shrink: 0; cursor: pointer;" onclick="document.getElementById('manual-label-color').click()"></div>
            <input type="color" id="manual-label-color" value="${currentManualLabel ? currentManualLabel.color : '#3cb44b'}" style="position: absolute; width: 0; height: 0; overflow: hidden; opacity: 0; pointer-events: none;" onchange="updateManualLabelColor(this.value)">
            <input type="text" id="manual-label-name" placeholder="Label name..." value="${currentManualLabel ? currentManualLabel.name : ''}" style="flex: 1; padding: 4px 8px; border: 1px solid #ccc; border-radius: 3px; font-size: 12px; color: #333; min-width: 0;">
            <button id="manual-label-set-btn" onclick="setCurrentManualLabel()" style="padding: 4px 10px; background: #667eea; color: white; border: none; border-radius: 4px; font-size: 12px; font-weight: 600; cursor: pointer; white-space: nowrap;" title="Create this label">Set</button>
        `;
        return;
    }

    // Schema compact mode — tree lives in the floating window
    container.innerHTML = `
        <span style="font-weight: 600; color: #333; font-size: 12px; white-space: nowrap;">Schema: ${activeSchema.name}</span>
        <button onclick="openSchemaFloat()" style="padding: 3px 10px; background: #667eea; color: white; border: none; border-radius: 3px; font-size: 11px; font-weight: 600; cursor: pointer; white-space: nowrap;">Browse &#9662;</button>
    `;
}

function renderSchemaTreeHTML(nodes, depth) {
    if (!nodes || nodes.length === 0) return '';
    let html = '';
    for (const node of nodes) {
        const hasChildren = node.children && node.children.length > 0;
        const indent = depth * 16;
        const caret = hasChildren
            ? `<span class="schema-caret" onclick="toggleSchemaNode(this)" style="cursor: pointer; display: inline-block; width: 14px; font-size: 10px; color: #888; user-select: none;">&#9654;</span>`
            : `<span style="display: inline-block; width: 14px;"></span>`;
        html += `<div class="schema-node" data-code="${node.code || ''}" data-name="${(node.name || '').toLowerCase()}" style="padding: 2px 0 2px ${indent}px; cursor: pointer; font-size: 12px; color: #333; border-radius: 2px;" onclick="selectSchemaLabel('${node.code || ''}', '${(node.name || '').replace(/'/g, "\\'")}', event)" onmouseover="this.style.background='#e8f0ff'" onmouseout="this.style.background=''">
                ${caret}<code style="font-size: 11px; color: #667eea; margin-right: 4px;">${node.code || ''}</code>${node.name || ''}
            </div>`;
        if (hasChildren) {
            html += `<div class="schema-children" style="display: none;">${renderSchemaTreeHTML(node.children, depth + 1)}</div>`;
        }
    }
    return html;
}

function toggleSchemaNode(caretEl) {
    event.stopPropagation();
    const parent = caretEl.closest('.schema-node');
    const children = parent.nextElementSibling;
    if (children && children.classList.contains('schema-children')) {
        const isOpen = children.style.display !== 'none';
        children.style.display = isOpen ? 'none' : 'block';
        caretEl.innerHTML = isOpen ? '&#9654;' : '&#9660;';
    }
}

function selectSchemaLabel(code, name, event) {
    if (event) event.stopPropagation();

    // If targeting a cluster input, fill it and return
    if (_schemaTargetInput) {
        const label = code ? `[${code}] ${name}` : name;
        _schemaTargetInput.value = label;
        _schemaTargetInput = null;
        closeSchemaFloat();
        return;
    }

    // Auto-assign color from SEG_PALETTE based on code hash or reuse existing
    const manualLabels = window.manualLabels;
    const SEG_PALETTE = window.SEG_PALETTE;
    const existingLabel = manualLabels.find(l => l.code === code && l.name === name);
    let color;
    if (existingLabel) {
        color = existingLabel.color;
    } else {
        // Hash code to pick from palette
        let hash = 0;
        for (let i = 0; i < code.length; i++) hash = ((hash << 5) - hash) + code.charCodeAt(i);
        color = SEG_PALETTE[Math.abs(hash) % SEG_PALETTE.length];
    }

    window.currentManualLabel = { name, color, code };
    // Update active label display
    const activeEl = document.getElementById('manual-active-label');
    if (activeEl) {
        activeEl.style.display = '';
        document.getElementById('manual-active-label-swatch').style.background = color;
        document.getElementById('manual-active-label-name').textContent = `${code ? '[' + code + '] ' : ''}${name}`;
        const acp = document.getElementById('active-label-color-picker');
        if (acp) acp.value = color;
    }
    localStorage.setItem('currentManualLabel_' + (window.currentViewportName || ''), JSON.stringify(window.currentManualLabel));
    closeSchemaFloat();
}

function filterSchemaTree(query) {
    const container = document.getElementById('schema-tree-container');
    if (!container) return;
    const q = query.toLowerCase().trim();

    if (!q) {
        // Show all, collapse all
        container.querySelectorAll('.schema-node').forEach(n => n.style.display = '');
        container.querySelectorAll('.schema-children').forEach(c => c.style.display = 'none');
        container.querySelectorAll('.schema-caret').forEach(c => c.innerHTML = '&#9654;');
        return;
    }

    // Show matching nodes and their ancestors
    const nodes = container.querySelectorAll('.schema-node');
    const childrenDivs = container.querySelectorAll('.schema-children');

    // First hide all
    nodes.forEach(n => n.style.display = 'none');
    childrenDivs.forEach(c => c.style.display = 'none');

    // Show matches and expand parents
    nodes.forEach(n => {
        const code = (n.dataset.code || '').toLowerCase();
        const name = n.dataset.name || '';
        if (code.includes(q) || name.includes(q)) {
            n.style.display = '';
            // Expand all parent schema-children
            let parent = n.parentElement;
            while (parent) {
                if (parent.classList && parent.classList.contains('schema-children')) {
                    parent.style.display = 'block';
                    // Show the node before this children div
                    if (parent.previousElementSibling && parent.previousElementSibling.classList.contains('schema-node')) {
                        parent.previousElementSibling.style.display = '';
                        const caret = parent.previousElementSibling.querySelector('.schema-caret');
                        if (caret) caret.innerHTML = '&#9660;';
                    }
                }
                parent = parent.parentElement;
            }
        }
    });
}

// ── Expose on window for onclick handlers and inline script access ──

window.toggleSchemaDropdown = toggleSchemaDropdown;
window.loadSchema = loadSchema;
window.loadCustomSchema = loadCustomSchema;
window.parseTabIndentedSchema = parseTabIndentedSchema;
window.renderSchemaSelector = renderSchemaSelector;
window.selectSchemaLabel = selectSchemaLabel;
window.filterSchemaTree = filterSchemaTree;
window.openSchemaForCluster = openSchemaForCluster;
window.openSchemaFloat = openSchemaFloat;
window.closeSchemaFloat = closeSchemaFloat;
window.toggleSchemaNode = toggleSchemaNode;
window.renderSchemaTreeHTML = renderSchemaTreeHTML;
