<script setup>
import { computed, ref } from "vue";

const token = ref(localStorage.getItem("tide.token") || "");
const profile = ref(JSON.parse(localStorage.getItem("tide.profile") || "null"));
const username = ref("admin");
const password = ref("123456");
const loginError = ref("");
const batches = ref([]);
const current = ref(null);
const title = ref("");
const rawText = ref("");
const formError = ref("");
const busy = ref(false);

const catalog = ref({ settings: null, entries: [], effective_disposition: "block", effective_disposition_name: "挡在未接纳" });
const dispositionChoice = ref("block");
const settingsChoice = ref("block");
const newCode = ref("");
const catalogError = ref("");
const settingsError = ref("");

const canWrite = computed(() => profile.value && profile.value.role === "writer");
const settingsLocked = computed(() => Boolean(catalog.value.settings && catalog.value.settings.locked_today));

const registeredLanded = computed(() => (current.value ? current.value.landed.filter((row) => !row.unregistered) : []));
const unregisteredLanded = computed(() => (current.value ? current.value.landed.filter((row) => row.unregistered) : []));
const unregisteredRejected = computed(() => (current.value ? current.value.rejected.filter((row) => row.unregistered) : []));
const unregisteredCodes = computed(() => {
  const codes = new Set(unregisteredLanded.value.map((row) => row.station_code));
  for (const row of unregisteredRejected.value) {
    const code = row.raw_line.split("|")[0].trim();
    if (code) codes.add(code);
  }
  return [...codes].sort();
});

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token.value) headers.Authorization = `Bearer ${token.value}`;
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = data.detail || "请求没有完成";
    throw new Error(typeof message === "string" ? message : "请求没有完成");
  }
  return data;
}

function applyCatalog(data) {
  catalog.value = data;
  settingsChoice.value = data.settings ? data.settings.disposition : data.effective_disposition;
  dispositionChoice.value = data.effective_disposition;
}

async function refresh() {
  const [batchList, catalogData] = await Promise.all([request("/api/batches"), request("/api/catalog")]);
  batches.value = batchList;
  applyCatalog(catalogData);
  if (!current.value && batches.value.length) {
    await openBatch(batches.value[0].id);
  }
}

async function openBatch(id) {
  current.value = await request(`/api/batches/${id}`);
}

async function submitLogin() {
  loginError.value = "";
  try {
    const data = await request("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username.value, password: password.value }),
    });
    token.value = data.access_token;
    profile.value = { username: data.username, role: data.role, title: data.title };
    localStorage.setItem("tide.token", token.value);
    localStorage.setItem("tide.profile", JSON.stringify(profile.value));
    await refresh();
  } catch (error) {
    loginError.value = error.message;
  }
}

async function submitBatch() {
  formError.value = "";
  busy.value = true;
  try {
    const data = await request("/api/batches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title.value, raw_text: rawText.value, disposition: dispositionChoice.value }),
    });
    title.value = "";
    rawText.value = "";
    await refresh();
    current.value = data;
  } catch (error) {
    formError.value = error.message;
  } finally {
    busy.value = false;
  }
}

async function saveSettings() {
  settingsError.value = "";
  try {
    applyCatalog(
      await request("/api/catalog/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ disposition: settingsChoice.value }),
      })
    );
  } catch (error) {
    settingsError.value = error.message;
  }
}

async function addEntry() {
  catalogError.value = "";
  try {
    applyCatalog(
      await request("/api/catalog/entries", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ station_code: newCode.value }),
      })
    );
    newCode.value = "";
  } catch (error) {
    catalogError.value = error.message;
  }
}

function logout() {
  token.value = "";
  profile.value = null;
  current.value = null;
  localStorage.removeItem("tide.token");
  localStorage.removeItem("tide.profile");
}

if (token.value) {
  refresh().catch(() => logout());
}
</script>

<template>
  <header class="top">
    <div>
      <p class="eyebrow">水文组 · 报文归集</p>
      <h1>潮位报文归集台</h1>
    </div>
    <div v-if="profile" class="who">
      <span>{{ profile.title }} {{ profile.username }}</span>
      <button class="ghost" @click="logout">离开</button>
    </div>
  </header>

  <main v-if="!profile" class="gate">
    <form @submit.prevent="submitLogin">
      <p>站码、时刻、潮位厘米、质量旗。一行一条，竖线分开。</p>
      <label>用户<input v-model="username" autocomplete="username" /></label>
      <label>口令<input v-model="password" type="password" autocomplete="current-password" /></label>
      <button>进入归集台</button>
      <p v-if="loginError" class="warn">{{ loginError }}</p>
    </form>
  </main>

  <main v-else class="desk">
    <div class="side">
      <section class="paste">
        <h2>贴入这一批</h2>
        <p class="rule">质量旗 A 实测，M 插补，B 可疑。同一站同一时刻只能落一次。目录外的编码不会悄悄入库。</p>
        <label>批次名<input v-model="title" :disabled="!canWrite" placeholder="例如 堡镇 11-13 午班" /></label>
        <textarea
          v-model="rawText"
          :disabled="!canWrite"
          placeholder="BZ03|202611130900|176|A"
          spellcheck="false"
        />
        <label>
          这批未入册编码的去向
          <select v-model="dispositionChoice" :disabled="!canWrite">
            <option value="block">挡在未接纳</option>
            <option value="land_and_mark">先落库再标未入册</option>
          </select>
        </label>
        <p class="rule">贴入的去向若与目录设置不一致，未入册的行会停住并记「目录去向不符」。</p>
        <button :disabled="!canWrite || busy" @click="submitBatch">归集这一批</button>
        <p v-if="!canWrite" class="quiet">档案员只能翻已落入的报文。</p>
        <p v-if="formError" class="warn">{{ formError }}</p>
      </section>

      <section class="catalog">
        <h2>目录设置</h2>
        <p class="rule">
          未入册去向：{{ catalog.effective_disposition_name }}
          <template v-if="catalog.settings">
            （{{ catalog.settings.set_by }} 定于 {{ catalog.settings.set_on }}<span v-if="settingsLocked">，今日已锁定</span>）
          </template>
          <template v-else>（尚未设定，默认挡在未接纳）</template>
        </p>
        <template v-if="canWrite">
          <label>
            定下今日去向
            <select v-model="settingsChoice" :disabled="settingsLocked">
              <option value="block">挡在未接纳</option>
              <option value="land_and_mark">先落库再标未入册</option>
            </select>
          </label>
          <button :disabled="settingsLocked" @click="saveSettings">定下，今天不再改</button>
          <p v-if="settingsError" class="warn">{{ settingsError }}</p>
          <label>新站码<input v-model="newCode" placeholder="例如 BZ03" @keyup.enter="addEntry" /></label>
          <button :disabled="!newCode.trim()" @click="addEntry">写进目录</button>
          <p v-if="catalogError" class="warn">{{ catalogError }}</p>
        </template>
        <p v-else class="quiet">档案员只能看目录，不能改。</p>
        <ul class="codes">
          <li v-for="entry in catalog.entries" :key="entry.station_code">
            <code>{{ entry.station_code }}</code>
            <span>{{ entry.created_by }} 收录</span>
          </li>
          <li v-if="!catalog.entries.length">目录还是空的</li>
        </ul>
      </section>
    </div>

    <section class="slips">
      <h2>已收列表</h2>
      <button
        v-for="batch in batches"
        :key="batch.id"
        class="slip"
        :class="{ on: current && current.batch.id === batch.id }"
        @click="openBatch(batch.id)"
      >
        <strong>{{ batch.title }}</strong>
        <em>{{ batch.status }}</em>
        <span>站码 {{ batch.station_codes.length ? batch.station_codes.join("、") : "—" }}</span>
        <span>落入 {{ batch.accepted_count }} 行 · 未入册 {{ batch.unregistered_count }} 行</span>
        <span>去向 {{ batch.disposition_name }}</span>
      </button>

      <article v-if="current" class="tape">
        <header>
          <h3>{{ current.batch.title }}</h3>
          <span>{{ current.batch.status }} · 去向 {{ current.batch.disposition_name }}</span>
        </header>

        <section class="unreg">
          <h4>未入册卡片</h4>
          <p v-if="!unregisteredLanded.length && !unregisteredRejected.length" class="quiet">这一批没有未入册的行。</p>
          <template v-else>
            <p class="rule">涉及站码：{{ unregisteredCodes.join("、") || "—" }}</p>
            <template v-if="unregisteredLanded.length">
              <h5>先落库的未入册行</h5>
              <table>
                <thead>
                  <tr><th>行</th><th>站</th><th>时刻</th><th>厘米</th><th>旗</th><th>未入册原因</th></tr>
                </thead>
                <tbody>
                  <tr v-for="row in unregisteredLanded" :key="row.line_no">
                    <td>{{ row.line_no }}</td>
                    <td>{{ row.station_code }}</td>
                    <td>{{ row.observed_at }}</td>
                    <td>{{ row.tide_cm }}</td>
                    <td>{{ row.flag_label }}</td>
                    <td>{{ row.unregistered_reason }}</td>
                  </tr>
                </tbody>
              </table>
            </template>
            <template v-if="unregisteredRejected.length">
              <h5>被挡下的未入册行</h5>
              <ul>
                <li v-for="row in unregisteredRejected" :key="row.line_no">
                  <code>{{ row.raw_line }}</code>
                  <span>{{ row.reason }}</span>
                </li>
              </ul>
            </template>
          </template>
        </section>

        <h4>已入册（已落库）</h4>
        <table>
          <thead>
            <tr><th>行</th><th>站</th><th>时刻</th><th>厘米</th><th>旗</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in registeredLanded" :key="row.line_no">
              <td>{{ row.line_no }}</td>
              <td>{{ row.station_code }}</td>
              <td>{{ row.observed_at }}</td>
              <td>{{ row.tide_cm }}</td>
              <td>{{ row.flag_label }}</td>
            </tr>
            <tr v-if="!registeredLanded.length"><td colspan="5">这一批没有已入册的落库行</td></tr>
          </tbody>
        </table>

        <h4>未接纳</h4>
        <ul>
          <li v-for="row in current.rejected" :key="row.line_no">
            <code>{{ row.raw_line }}</code>
            <span><b v-if="row.unregistered" class="tag">未入册</b>{{ row.reason }}</span>
          </li>
          <li v-if="!current.rejected.length">没有被挡下的行</li>
        </ul>
      </article>
    </section>
  </main>
</template>

<style scoped>
.top, .desk, .gate { width: min(1180px, calc(100% - 32px)); margin: 0 auto; }
.top { display: flex; justify-content: space-between; align-items: end; padding: 28px 0 12px; }
.eyebrow { letter-spacing: 0.18em; text-transform: uppercase; color: #d7a15f; font-size: 12px; margin: 0 0 6px; }
h1 { margin: 0; font-weight: 560; font-size: 34px; }
h2, h3, h4, h5 { margin: 0; }
.who { display: flex; gap: 12px; align-items: center; }
.ghost { background: transparent; color: #f3efe2; border: 1px solid rgba(243, 239, 226, 0.35); padding: 6px 12px; }
.gate { padding: 24px 0 48px; }
.gate form, .paste, .catalog, .tape {
  background: #f3efe2;
  color: #1c2430;
  padding: 22px;
}
.gate form { width: min(420px, 100%); display: grid; gap: 12px; }
label { display: grid; gap: 6px; font-size: 14px; }
input, textarea, select {
  width: 100%;
  border: 0;
  background: #fffdf8;
  color: #1c2430;
  padding: 10px 12px;
  box-shadow: inset 0 0 0 1px #d9d0bc;
}
textarea { min-height: 220px; font-family: "IBM Plex Mono", Consolas, monospace; line-height: 1.6; }
button { background: #9d2c2c; color: #fffaf2; padding: 10px 14px; }
.desk { display: grid; grid-template-columns: 360px 1fr; gap: 22px; padding-bottom: 48px; }
.side, .paste, .catalog, .slips { display: grid; gap: 12px; align-content: start; }
.side { gap: 22px; }
.rule, .quiet { margin: 0; color: #5c5648; font-size: 14px; }
.slip {
  text-align: left;
  background: transparent;
  color: #f3efe2;
  border: 1px solid rgba(243, 239, 226, 0.2);
  padding: 12px 14px;
  display: grid;
  gap: 4px;
}
.slip.on { border-color: #d7a15f; }
.slip em { font-style: normal; color: #d7a15f; }
.slip span, .tape li span { color: #5c5648; }
.slip span { color: rgba(243, 239, 226, 0.72); }
.tape header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.tape h4 { margin: 14px 0 8px; }
.tape h5 { margin: 10px 0 6px; font-size: 14px; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 6px 4px; border-bottom: 1px solid #e4dcc8; font-size: 14px; }
ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; }
li { display: grid; gap: 2px; }
code { font-family: Consolas, monospace; }
.warn { color: #9d2c2c; margin: 0; }
.codes li { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 1px dashed #e4dcc8; padding: 4px 0; }
.codes li span { color: #5c5648; font-size: 13px; }
.unreg {
  border: 1px solid #d7a15f;
  background: #fbf4e4;
  padding: 14px;
  margin: 10px 0 4px;
  display: grid;
  gap: 8px;
}
.tag {
  background: #d7a15f;
  color: #1c2430;
  font-size: 12px;
  padding: 1px 6px;
  margin-right: 6px;
}
@media (max-width: 900px) {
  .desk { grid-template-columns: 1fr; }
}
</style>
