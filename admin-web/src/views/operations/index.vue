<template>
  <div v-loading="loading" class="ops-page">
    <!-- 页头 -->
    <div class="page-head">
      <div class="page-title">巡查计划与运行情况</div>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb16" />

    <!-- 执行情况概览 -->
    <div class="stats-grid">
      <el-card shadow="never" class="stat-card">
        <div class="stat-value">{{ percent(tasks.coverage) }}</div>
        <div class="stat-label">执行覆盖率</div>
        <div class="stat-hint">已完成（含补检）占应检任务的比例</div>
      </el-card>
      <el-card shadow="never" class="stat-card">
        <div class="stat-value">{{ percent(tasks.on_time_rate) }}</div>
        <div class="stat-label">按时完成率</div>
        <div class="stat-hint">按期完成占已完成任务的比例</div>
      </el-card>
      <el-card shadow="never" class="stat-card">
        <div class="stat-value danger">{{ tasks.counts?.missed || 0 }}</div>
        <div class="stat-label">漏检任务</div>
        <div class="stat-hint">已过截止日期仍未执行</div>
      </el-card>
      <el-card shadow="never" class="stat-card">
        <div class="stat-value warning">{{ tasks.counts?.late || 0 }}</div>
        <div class="stat-label">补检任务</div>
        <div class="stat-hint">逾期后补做完成的任务</div>
      </el-card>
    </div>

    <!-- 操作区：创建计划 + 人员交接 -->
    <el-row :gutter="16" class="mb16">
      <el-col :xs="24" :lg="14">
        <el-card shadow="never">
          <template #header><span class="card-title">创建巡查计划</span></template>
          <el-form label-position="top">
            <div class="form-grid two">
              <el-form-item label="计划名称">
                <el-input v-model="form.name" maxlength="100" placeholder="如：1 号楼周检计划" />
              </el-form-item>
              <el-form-item label="检查项">
                <el-select v-model="form.item_id" filterable placeholder="选择检查项" style="width: 100%">
                  <el-option v-for="i in items" :key="i.id" :label="i.name" :value="i.id" />
                </el-select>
              </el-form-item>
              <el-form-item label="巡查员">
                <el-select v-model="form.inspector_id" placeholder="选择巡查员" style="width: 100%">
                  <el-option v-for="m in inspectors" :key="m.user_id" :label="m.name" :value="m.user_id" />
                </el-select>
              </el-form-item>
              <el-form-item label="间隔天数">
                <el-input-number v-model="form.interval_days" :min="1" :max="365" controls-position="right" style="width: 100%" />
              </el-form-item>
              <el-form-item label="开始日期">
                <el-date-picker v-model="form.start_date" value-format="YYYY-MM-DD" type="date" placeholder="开始日期" style="width: 100%" />
              </el-form-item>
              <el-form-item label="结束日期">
                <el-date-picker v-model="form.end_date" value-format="YYYY-MM-DD" type="date" placeholder="结束日期" style="width: 100%" />
              </el-form-item>
            </div>
            <div class="form-foot">
              <el-button type="primary" :loading="saving" @click="create">创建计划</el-button>
            </div>
          </el-form>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="10">
        <el-card shadow="never">
          <template #header><span class="card-title">人员交接</span></template>
          <p class="card-desc">移交未闭环工单及未开始的巡查任务，原始证据作者不变。</p>
          <el-form label-position="top">
            <el-form-item label="角色">
              <el-radio-group v-model="handoff.role">
                <el-radio-button value="inspector">巡查员</el-radio-button>
                <el-radio-button value="rectifier">整改责任人</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="原人员">
              <el-select v-model="handoff.from_user" placeholder="选择原人员" style="width: 100%">
                <el-option v-for="m in members.filter(m => m.role === handoff.role)" :key="m.member_id" :label="m.name" :value="m.user_id" />
              </el-select>
            </el-form-item>
            <el-form-item label="接替人">
              <el-select v-model="handoff.to_user" placeholder="选择接替人" style="width: 100%">
                <el-option v-for="m in members.filter(m => m.role === handoff.role && m.active)" :key="m.member_id" :label="m.name" :value="m.user_id" />
              </el-select>
            </el-form-item>
            <el-form-item label="交接原因">
              <el-input v-model="handoff.reason" maxlength="500" type="textarea" :rows="2" placeholder="填写交接原因" />
            </el-form-item>
            <div class="form-foot">
              <el-button type="primary" plain @click="transfer">执行交接</el-button>
            </div>
          </el-form>
        </el-card>
      </el-col>
    </el-row>

    <!-- 计划列表 -->
    <el-card shadow="never" class="mb16">
      <template #header><span class="card-title">计划列表</span></template>
      <el-table v-if="plans.length" :data="plans" border stripe>
        <el-table-column prop="name" label="计划" min-width="180" show-overflow-tooltip />
        <el-table-column label="起止周期" min-width="200">
          <template #default="{ row }">{{ row.start_date }} ~ {{ row.end_date }}</template>
        </el-table-column>
        <el-table-column prop="interval_days" label="间隔天数" width="100" align="center" />
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="center">
          <template #default="{ row }">
            <el-button v-if="row.enabled" link type="danger" @click="disable(row)">停用</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无计划" :image-size="80" />
    </el-card>

    <!-- 执行任务明细 -->
    <el-card shadow="never" class="mb16">
      <template #header><span class="card-title">执行任务明细</span></template>
      <el-table v-if="tasks.list && tasks.list.length" :data="tasks.list" border stripe>
        <el-table-column prop="plan_name" label="计划" min-width="180" show-overflow-tooltip />
        <el-table-column prop="scheduled_date" label="日期" width="110" />
        <el-table-column label="巡查员" min-width="100">
          <template #default="{ row }">{{ memberName(row.inspector_id) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="TASK_STATUS_TAG[row.status] || 'info'">{{ TASK_STATUS[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="关联记录" width="100" align="center">
          <template #default="{ row }">{{ row.record_id ? '#' + row.record_id : '—' }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无执行任务" :image-size="80" />
    </el-card>

    <!-- AI 用量 -->
    <el-card shadow="never">
      <template #header><span class="card-title">AI 用量（按 UTC 日统计）</span></template>
      <el-alert v-if="usage.alert" title="AI 用量已达到日额度的80%，请安排人工巡查" type="warning" :closable="false" show-icon class="mb16" />
      <div class="usage-bar">
        <span class="usage-item">今日请求 <b>{{ usage.used || 0 }} / {{ usage.limit || 0 }}</b></span>
        <span class="usage-item">用户日限额 <b>{{ usage.user_limit ?? '—' }}</b></span>
        <span class="usage-item">项目并发上限 <b>{{ usage.concurrency_limit ?? '—' }}</b></span>
      </div>
      <p class="usage-tip">费用按配置的模型单价估算；空白表示服务商未返回用量或尚未配置单价。</p>
      <el-table v-if="usage.list && usage.list.length" :data="usage.list" border stripe>
        <el-table-column label="时间" min-width="150">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="model" label="模型" min-width="150" show-overflow-tooltip />
        <el-table-column prop="status" label="状态" width="100" align="center" />
        <el-table-column prop="input_tokens" label="输入 Token" width="110" align="right" />
        <el-table-column prop="output_tokens" label="输出 Token" width="110" align="right" />
        <el-table-column prop="cost_cny" label="估算费用（元）" width="130" align="right" />
      </el-table>
      <el-empty v-else description="暂无用量记录" :image-size="80" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import request from '../../utils/request';
import { fmtTime } from '../../utils/dict';

const members = ref([]), handoff = ref({ from_user: null, to_user: null, role: 'inspector', reason: '' });
const form = ref({ name: '', item_id: null, inspector_id: null, start_date: '', end_date: '', interval_days: 1 });
const items = ref([]), inspectors = ref([]), plans = ref([]), tasks = ref({}), usage = ref({}), error = ref(''), saving = ref(false), loading = ref(false);

const TASK_STATUS = { pending: '待检', missed: '漏检', done: '已完成', late: '补检', cancelled: '已取消' };
const TASK_STATUS_TAG = { pending: 'info', missed: 'danger', done: 'success', late: 'warning', cancelled: 'info' };
const percent = (v) => (v == null ? '—' : (v * 100).toFixed(1) + '%');
const memberName = (uid) => {
  const m = members.value.find((x) => x.user_id === uid);
  return m ? m.name : '#' + uid;
};

async function load() {
  loading.value = true;
  error.value = '';
  try {
    const requests = [['plans', () => request.get('/api/plans'), plans], ['tasks', () => request.get('/api/plan-tasks'), tasks], ['usage', () => request.get('/api/operations/ai-usage'), usage]];
    for (const [, fn, target] of requests) {
      try { target.value = await fn(); } catch (e) { error.value = '部分数据获取失败，请确认当前项目权限和网络后刷新'; }
    }
    try {
      const all = []; let page = 1, total = 1;
      while (all.length < total) { const r = await request.get('/api/inspection-items', { params: { page, page_size: 100 } }); all.push(...r.list); total = r.total; if (!r.list.length) break; page++; }
      items.value = all;
      members.value = await request.get('/api/admin/members');
      inspectors.value = members.value.filter((m) => m.active && m.role === 'inspector');
    } catch (e) { error.value = '检查项或成员获取失败，请刷新'; }
  } finally {
    loading.value = false;
  }
}

async function create() {
  if (saving.value) return;
  const f = form.value;
  if (!f.name.trim() || !f.item_id || !f.inspector_id || !f.start_date || !f.end_date) { ElMessage.warning('请填写计划名称、检查项、巡查员及起止日期'); return; }
  if (f.end_date < f.start_date) { ElMessage.warning('结束日期不能早于开始日期'); return; }
  saving.value = true; error.value = '';
  try { await request.post('/api/plans', { ...f, name: f.name.trim() }); ElMessage.success('计划已创建'); await load(); }
  catch (e) { error.value = e.message || '计划创建失败，请重试'; }
  finally { saving.value = false; }
}

async function disable(p) {
  try { await ElMessageBox.confirm('停用后取消尚未开始的待检任务，历史执行记录保留。', '停用计划'); await request.post('/api/plans/' + p.id + '/disable'); await load(); }
  catch (e) { if (e !== 'cancel' && e !== 'close') error.value = e.message || '停用失败，请重试'; }
}

async function transfer() {
  const h = handoff.value;
  if (!h.from_user || !h.to_user || h.from_user === h.to_user || !h.reason.trim()) { ElMessage.warning('请选择不同的原人员和接替人，并填写交接原因'); return; }
  try { await ElMessageBox.confirm('确认移交当前项目该成员的全部未闭环工作？原始巡查证据作者不变。', '人员交接'); await request.post('/api/operations/handoff', h); ElMessage.success('交接完成'); await load(); }
  catch (e) { if (e !== 'cancel' && e !== 'close') error.value = e.message || '交接失败，请重试'; }
}

onMounted(load);
</script>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.page-title {
  font-size: 20px;
  font-weight: 700;
  color: #1a2634;
}
.mb16 { margin-bottom: 16px; }

.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}
@media (max-width: 992px) {
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
}
.stat-card { text-align: center; }
.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: #1a2634;
  line-height: 1.4;
}
.stat-value.danger { color: #e5652a; }
.stat-value.warning { color: #e6a23c; }
.stat-label {
  font-size: 13px;
  color: #5b6b7d;
  margin-top: 4px;
}
.stat-hint {
  font-size: 12px;
  color: #98a3b3;
  line-height: 1.5;
  margin-top: 6px;
  min-height: 34px;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  color: #1a2634;
}
.card-title::before {
  content: '';
  display: inline-block;
  width: 4px;
  height: 14px;
  background: #1e5fd0;
  border-radius: 2px;
  margin-right: 8px;
  vertical-align: -2px;
}
.card-desc {
  font-size: 13px;
  color: #5b6b7d;
  margin: -4px 0 16px;
  line-height: 1.6;
}

.form-grid {
  display: grid;
  gap: 4px 24px;
}
.form-grid.two { grid-template-columns: repeat(2, 1fr); }
@media (max-width: 992px) {
  .form-grid.two { grid-template-columns: 1fr; }
}
.form-foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 4px;
}

.usage-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  margin-bottom: 8px;
}
.usage-item { font-size: 14px; color: #5b6b7d; }
.usage-item b { color: #1a2634; }
.usage-tip {
  font-size: 12px;
  color: #98a3b3;
  margin: 8px 0 16px;
}
</style>