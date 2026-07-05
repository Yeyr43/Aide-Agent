"""OnboardingScreen CSS — 从 onboarding.py 拆分。"""

ONBOARDING_CSS = """
OnboardingScreen {
    background: #0c0c0c;
    align: center middle;
}

#onboard-container {
    width: 56;
    height: auto;
    padding: 2 3;
}

#onboard-title {
    color: #c8c8c0;
    text-style: bold;
    margin-bottom: 2;
    text-align: center;
    width: 100%;
}

.onboard-label {
    color: #888888;
    margin-bottom: 1;
}

.onboard-input {
    width: 100%;
    margin-bottom: 1;
    background: #121212;
    color: #c8c8c0;
    border: solid #2a2a3a;
    padding: 0 1;
}
.onboard-input:focus {
    border: solid #7ec8e3;
}

.onboard-textarea {
    width: 100%;
    height: 4;
    margin-bottom: 1;
    background: #121212;
    color: #c8c8c0;
    border: solid #2a2a3a;
}
.onboard-textarea:focus {
    border: solid #7ec8e3;
}

.onboard-hint {
    color: #555555;
    margin-bottom: 1;
}

/* ── 底部导航：三栏同行 ── */
#onboard-nav {
    width: 100%;
    height: auto;
    margin-top: 2;
}

#onboard-nav Container {
    height: auto;
}

#nav-prev-area {
    width: 1fr;
    content-align: left middle;
}

#nav-page-area {
    width: 1fr;
    content-align: center middle;
}

#nav-next-area {
    width: 1fr;
    content-align: right middle;
}

#onboard-nav Button {
    border: none;
    background: transparent;
    color: #7ec8e3;
    min-width: 10;
    padding: 0 1;
}
#onboard-nav Button:hover {
    color: #c8c8c0;
}

#nav-page {
    color: #555555;
}

#onboard-page-indicator {
    text-align: center;
    width: 100%;
    margin-top: 1;
    color: #444444;
}

#lang-row {
    width: 100%;
    height: auto;
    margin: 2 0;
    align: center middle;
}

.lang-btn {
    width: 1fr;
    margin: 0 1;
    padding: 1 0;
    border: solid #7ec8e3;
    background: #121212;
    color: #7ec8e3;
    text-style: bold;
    content-align: center middle;
}
.lang-btn:hover {
    border: solid #00d4ff;
    background: #1a2a3a;
    color: #00d4ff;
}

#field-vision-toggle {
    border: none;
    background: transparent;
    color: #7ec8e3;
    min-width: 16;
    padding: 0 1;
    margin-bottom: 1;
}
#field-vision-toggle:hover {
    color: #c8c8c0;
}

#role-row {
    width: 100%;
    height: auto;
    margin: 2 0;
    align: center middle;
}

.role-btn {
    width: 1fr;
    margin: 0 0 1 0;
    padding: 1 0;
    border: solid #7ec8e3;
    background: #121212;
    color: #7ec8e3;
    text-style: bold;
    content-align: center middle;
}
.role-btn:hover {
    border: solid #00d4ff;
    background: #1a2a3a;
    color: #00d4ff;
}

.role-skip-btn {
    width: 1fr;
    margin: 1 0 0 0;
    padding: 1 0;
    border: none;
    background: transparent;
    color: #555555;
    content-align: center middle;
}
.role-skip-btn:hover {
    color: #c8c8c0;
}
"""
