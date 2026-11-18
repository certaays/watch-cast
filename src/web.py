from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.config import JobConfig, load_config, save_config

CONFIG_PATH = Path("jobs.yaml")
app = FastAPI(title="WatchCast Dashboard")


class CreateJobRequest(BaseModel):
    name: str
    type: str
    schedule: str
    target_chat_id: str | None = None
    params: dict[str, Any]


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    config = load_config(CONFIG_PATH)
    return [job.model_dump() for job in config.jobs]


@app.post("/api/jobs")
def add_job(payload: CreateJobRequest) -> dict[str, str]:
    config = load_config(CONFIG_PATH)
    new_job = JobConfig(
        name=payload.name,
        type=payload.type,
        schedule=payload.schedule,
        enabled=True,
        target_chat_id=payload.target_chat_id or None,
        params=payload.params,
    )
    config.jobs.append(new_job)
    save_config(config, CONFIG_PATH)
    return {"status": "ok"}


@app.put("/api/jobs/{index}")
def update_job(index: int, payload: CreateJobRequest) -> dict[str, str]:
    config = load_config(CONFIG_PATH)
    if index < 0 or index >= len(config.jobs):
        raise HTTPException(status_code=404, detail="Job not found")
    existing = config.jobs[index]
    config.jobs[index] = JobConfig(
        name=payload.name,
        type=payload.type,
        schedule=payload.schedule,
        enabled=existing.enabled,
        target_chat_id=payload.target_chat_id or None,
        params=payload.params,
    )
    save_config(config, CONFIG_PATH)
    return {"status": "updated"}


@app.post("/api/jobs/{index}/toggle")
def toggle_job(index: int) -> dict[str, Any]:
    config = load_config(CONFIG_PATH)
    if index < 0 or index >= len(config.jobs):
        raise HTTPException(status_code=404, detail="Job not found")
    config.jobs[index].enabled = not config.jobs[index].enabled
    save_config(config, CONFIG_PATH)
    return {"enabled": config.jobs[index].enabled}


@app.delete("/api/jobs/{index}")
def delete_job(index: int) -> dict[str, str]:
    config = load_config(CONFIG_PATH)
    if index < 0 or index >= len(config.jobs):
        raise HTTPException(status_code=404, detail="Job not found")
    config.jobs.pop(index)
    save_config(config, CONFIG_PATH)
    return {"status": "deleted"}


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>WatchCast Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen p-6">
  <div class="max-w-4xl mx-auto space-y-8">
    <header class="flex items-center justify-between border-b border-slate-800 pb-4">
      <div>
        <h1 class="text-2xl font-bold text-sky-400">WatchCast</h1>
        <p class="text-sm text-slate-400">Manage your Telegram notification jobs</p>
      </div>
      <button onclick="openModal()" class="bg-sky-500 hover:bg-sky-600 text-slate-950 font-semibold rounded-lg px-4 py-2 text-sm">
        + Add Job
      </button>
    </header>

    <section class="space-y-3">
      <h2 class="text-lg font-semibold">Scheduled Jobs</h2>
      <div id="jobsList" class="space-y-3"></div>
    </section>
  </div>

  <!-- Add / Edit Modal -->
  <div id="jobModal" class="hidden fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50">
    <div class="bg-slate-900 border border-slate-800 rounded-xl p-6 w-full max-w-xl space-y-4">
      <div class="flex items-center justify-between">
        <h2 id="modalTitle" class="text-lg font-semibold">Add New Job</h2>
        <button onclick="closeModal()" class="text-slate-400 hover:text-slate-200 text-xl">&times;</button>
      </div>
      <form id="jobForm" class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <input id="name" placeholder="Job Name (e.g. Sony XM5)" required class="bg-slate-800 rounded-lg px-3 py-2 text-sm" />
        <select id="type" onchange="toggleFields()" class="bg-slate-800 rounded-lg px-3 py-2 text-sm">
          <option value="price_drop">Price Drop</option>
          <option value="job_feed">Job Feed</option>
        </select>
        <input id="schedule" value="*/30 * * * *" placeholder="Cron (e.g. */30 * * * *)" required class="bg-slate-800 rounded-lg px-3 py-2 text-sm" />
        <input id="chatId" placeholder="Target Chat ID (optional, e.g. @deals)" class="bg-slate-800 rounded-lg px-3 py-2 text-sm" />

        <div id="priceFields" class="md:col-span-2 grid grid-cols-1 md:grid-cols-3 gap-4">
          <input id="url" placeholder="Product URL" class="bg-slate-800 rounded-lg px-3 py-2 text-sm" />
          <input id="selector" placeholder="CSS Selector (e.g. .price)" class="bg-slate-800 rounded-lg px-3 py-2 text-sm" />
          <input id="targetPrice" type="number" step="0.01" placeholder="Target Price" class="bg-slate-800 rounded-lg px-3 py-2 text-sm" />
        </div>

        <div id="jobFields" class="hidden md:col-span-2 grid grid-cols-1 md:grid-cols-3 gap-4">
          <input id="source" value="remotive" placeholder="Source (remotive, jsearch, etc.)" class="bg-slate-800 rounded-lg px-3 py-2 text-sm" />
          <input id="keyword" placeholder="Keyword (e.g. python)" class="bg-slate-800 rounded-lg px-3 py-2 text-sm" />
          <input id="country" placeholder="Country (optional, e.g. id)" class="bg-slate-800 rounded-lg px-3 py-2 text-sm" />
        </div>

        <div class="md:col-span-2 flex justify-end gap-2 pt-2">
          <button type="button" onclick="closeModal()" class="px-4 py-2 rounded-lg bg-slate-800 text-slate-300 text-sm">Cancel</button>
          <button id="submitBtn" type="submit" class="bg-sky-500 hover:bg-sky-600 text-slate-950 font-semibold rounded-lg px-4 py-2 text-sm">
            Add Job
          </button>
        </div>
      </form>
    </div>
  </div>

  <script>
    let editingIndex = null;
    let cachedJobs = [];

    function toggleFields() {
      const type = document.getElementById('type').value;
      document.getElementById('priceFields').classList.toggle('hidden', type !== 'price_drop');
      document.getElementById('jobFields').classList.toggle('hidden', type !== 'job_feed');
    }

    async function loadJobs() {
      const res = await fetch('/api/jobs');
      cachedJobs = await res.json();
      const list = document.getElementById('jobsList');
      list.innerHTML = cachedJobs.map((j, i) => `
        <div class="bg-slate-900 border border-slate-800 rounded-xl p-4 flex items-center justify-between">
          <div>
            <div class="font-semibold">${j.name} <span class="text-xs px-2 py-0.5 rounded bg-slate-800 text-sky-400 ml-2">${j.type}</span></div>
            <div class="text-xs text-slate-400 mt-1">Cron: <code>${j.schedule}</code> · Target: ${j.target_chat_id || 'DEFAULT_CHAT_ID'}</div>
          </div>
          <div class="flex items-center gap-2">
            <button onclick="editJob(${i})" class="text-xs px-3 py-1.5 rounded-lg bg-sky-500/20 text-sky-300 hover:bg-sky-500/30">
              Edit
            </button>
            <button onclick="toggleJob(${i})" class="text-xs px-3 py-1.5 rounded-lg ${j.enabled ? 'bg-emerald-500/20 text-emerald-300' : 'bg-slate-800 text-slate-400'}">
              ${j.enabled ? 'Enabled' : 'Paused'}
            </button>
            <button onclick="deleteJob(${i})" class="text-xs px-3 py-1.5 rounded-lg bg-rose-500/20 text-rose-300 hover:bg-rose-500/30">
              Delete
            </button>
          </div>
        </div>
      `).join('');
    }

    function openModal() {
      editingIndex = null;
      document.getElementById('jobForm').reset();
      document.getElementById('modalTitle').textContent = 'Add New Job';
      document.getElementById('submitBtn').textContent = 'Add Job';
      toggleFields();
      document.getElementById('jobModal').classList.remove('hidden');
    }

    function closeModal() {
      document.getElementById('jobModal').classList.add('hidden');
    }

    function editJob(i) {
      const j = cachedJobs[i];
      editingIndex = i;
      document.getElementById('name').value = j.name;
      document.getElementById('type').value = j.type;
      document.getElementById('schedule').value = j.schedule;
      document.getElementById('chatId').value = j.target_chat_id || '';
      toggleFields();
      if (j.type === 'price_drop') {
        document.getElementById('url').value = j.params.url || '';
        document.getElementById('selector').value = j.params.css_selector || '';
        document.getElementById('targetPrice').value = j.params.target_price || '';
      } else {
        document.getElementById('source').value = j.params.source || 'remotive';
        document.getElementById('keyword').value = j.params.keyword || '';
        document.getElementById('country').value = j.params.country || '';
      }
      document.getElementById('modalTitle').textContent = 'Edit Job';
      document.getElementById('submitBtn').textContent = 'Save Changes';
      document.getElementById('jobModal').classList.remove('hidden');
    }

    document.getElementById('jobForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const type = document.getElementById('type').value;
      const params = type === 'price_drop'
        ? {
            url: document.getElementById('url').value,
            css_selector: document.getElementById('selector').value,
            target_price: parseFloat(document.getElementById('targetPrice').value || '0')
          }
        : {
            source: document.getElementById('source').value,
            keyword: document.getElementById('keyword').value,
            ...(document.getElementById('country').value ? { country: document.getElementById('country').value } : {}),
            limit: 10
          };

      const endpoint = editingIndex === null ? '/api/jobs' : `/api/jobs/${editingIndex}`;
      const method = editingIndex === null ? 'POST' : 'PUT';
      await fetch(endpoint, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: document.getElementById('name').value,
          type,
          schedule: document.getElementById('schedule').value,
          target_chat_id: document.getElementById('chatId').value || null,
          params
        })
      });
      editingIndex = null;
      document.getElementById('submitBtn').textContent = 'Add Job';
      closeModal();
      loadJobs();
    });

    async function toggleJob(i) {
      await fetch(`/api/jobs/${i}/toggle`, { method: 'POST' });
      loadJobs();
    }

    async function deleteJob(i) {
      await fetch(`/api/jobs/${i}`, { method: 'DELETE' });
      loadJobs();
    }

    loadJobs();
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return DASHBOARD_HTML

