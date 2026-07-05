"""ApiConfigScreen CSS — 从 api_config.py 拆分。"""

API_CONFIG_CSS = """
ApiConfigScreen {
    background: #0c0c0c;
    align: center middle;
}

#api-config-container {
    width: 56;
    height: auto;
    padding: 2 3;
}

#api-config-title {
    color: #c8c8c0;
    text-style: bold;
    margin-bottom: 2;
    text-align: center;
    width: 100%;
}

.api-label {
    color: #888888;
    margin-bottom: 1;
}

.api-input {
    width: 100%;
    margin-bottom: 1;
    background: #121212;
    color: #c8c8c0;
    border: solid #2a2a3a;
    padding: 0 1;
}
.api-input:focus {
    border: solid #7ec8e3;
}

#api-vision-toggle {
    border: none;
    background: transparent;
    color: #7ec8e3;
    min-width: 16;
    padding: 0 1;
    margin-bottom: 1;
}
#api-vision-toggle:hover {
    color: #c8c8c0;
}

#api-error {
    color: #e06060;
    margin-bottom: 1;
    text-align: center;
    width: 100%;
    height: 1;
    visibility: hidden;
}
#api-error.visible {
    visibility: visible;
}

#api-btn-row {
    width: 100%;
    height: auto;
    margin-top: 1;
    align: center middle;
}

#api-btn-row Button {
    margin: 0 1;
    min-width: 14;
}
#api-btn-save {
    background: #1a3a2a;
    border: solid #7ec8e3;
    color: #7ec8e3;
}
#api-btn-save:hover {
    background: #2a5a3a;
    color: #00d4ff;
}
#api-btn-cancel {
    background: transparent;
    border: solid #444444;
    color: #888888;
}

.api-hint {
    color: #555555;
    margin-bottom: 1;
}
"""
