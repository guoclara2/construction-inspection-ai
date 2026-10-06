<template>
  <el-card shadow="never">
    <!-- 筛选区：状态 / 通道 / 模板 -->
    <el-form inline class="filter-form">
      <el-form-item label="状态">
        <el-select v-model="filters.status" placeholder="全部" clearable aria-label="按发送状态筛选" style="width: 120px">
          <el-option v-for="(label, value) in NOTIFY_STATUS" :key="value" :label="label" :value="value" />
        </el-select>
      </el-form-item>
      <el-form-item label="通道">
        <el-select v-model="filters.channel" placeholder="全部" clearable aria-label="按发送通道筛选" style="width: 120px">
          <el-option v-for="(label, value) in NOTIFY_CHANNEL" :key="value" :label="label" :value="value" />
        </el-select>
      </el-form-item>
      <el-form-item label="模板">
        <el-select v-model="filters.template_code" placeholder="全部" clearable filterable aria-label="按通知模板筛选" style="width: 240px">
          <el-option v-for="t in templates" :key="t.code" :label="t.label" :value="t.code" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="search">查询</el-button>
      </el-form-item>
    </el-form>

    <!-- 通知记录表 -->
    <el-table :data="rows" v-loading="loading" border stripe>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="接收人" min-width="90">
        <template #default="{ row }">
          {{ row.user_name }}
          <span class="muted"> #{{ row.user_id }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="title" label="标题" min-width="170" show-overflow-tooltip />
      <el-table-column prop="content" label="内容" min-width="220" show-overflow-tooltip />
      <el-table-column label="模板" min-width="150">
        <template #default="{ row }">{{ templateLabel(row.template_code) }}</template>
      </el-table-column>
      <el-table-column label="通道" width="90" align="center">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">{{ NOTIFY_CHANNEL[row.channel] || row.channel }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="STATUS_TAG[row.status]">{{ NOTIFY_STATUS[row.status] || row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="重试" width="70" align="center">
        <template #default="{ row }">{{ row.retry_count }}</template>
      </el-table-column>
      <el-table-column label="失败原因" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">{{ row.error || '—' }}</template>
      </el-table-column>
      <el-table-column label="创建时间" min-width="130">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row.status === 'failed' || row.status === 'skipped'"
            link type="primary" @click="resend(row)">重发</el-button>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination v-model:current-page="page" :page-size="pageSize" :total="total"
        layout="total, prev, pager, next" @current-change="load" />
    </div>
  </el-card>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import request from '../../utils/request';
import { fmtTime } from '../../utils/dict';

// 通知状态/通道中文映射（P06 §2.5.2）
const NOTIFY_STATUS = { pending: '待发送', sent: '已发送', failed: '失败', skipped: '已跳过' };
const STATUS_TAG = { pending: 'info', sent: 'success', failed: 'danger', skipped: 'warning' };
const NOTIFY_CHANNEL = { inapp: '站内', wechat: '微信', sms: '短信' };

// 模板下拉（与种子 14 个模板对应；后端未提供模板列表接口，前端静态映射 + code 兜底）
const templates = [
  { code: 'ORDER_CREATED', label: '新整改工单' },
  { code: 'ORDER_ACCEPTED', label: '工单已接收' },
  { code: 'ORDER_STARTED', label: '工单开始整改' },
  { code: 'ORDER_FEEDBACK', label: '工单待审核' },
  { code: 'ORDER_REVIEW_PASS', label: '工单已闭环' },
  { code: 'ORDER_REVIEW_REJECT', label: '工单被打回' },
  { code: 'ORDER_URGE', label: '工单催办提醒' },
  { code: 'ORDER_WARN', label: '工单临期提醒' },
  { code: 'ORDER_OVERDUE', label: '工单已超期' },
  { code: 'ORDER_ESCALATE', label: '工单超期升级' },
  { code: 'ORDER_TRANSFER', label: '工单已改派' },
  { code: 'ORDER_EXTEND_RESULT', label: '延期审批结果' },
  { code: 'PLAN_TASK_DUE', label: '巡查任务到期' },
  { code: 'PLAN_TASK_MISSED', label: '巡查任务漏检' },
];

function templateLabel(code) {
  if (!code) return '—';
  const hit = templates.filter((t) => t.code === code)[0];
  return hit ? hit.label : code;
}

const rows = ref([]);
const page = ref(1);
const pageSize = 20;
const total = ref(0);
const loading = ref(false);
const filters = reactive({ status: '', channel: '', template_code: '' });

function load() {
  loading.value = true;
  const params = { page: page.value, page_size: pageSize };
  if (filters.status) params.status = filters.status;
  if (filters.channel) params.channel = filters.channel;
  if (filters.template_code) params.template_code = filters.template_code;
  request.get('/api/admin/notifications', { params })
    .then((data) => {
      rows.value = data.list || [];
      total.value = data.total || 0;
    })
    .catch(() => {})
    .finally(() => {
      loading.value = false;
    });
}

function search() {
  page.value = 1;
  load();
}

// 手动重发失败/跳过的通知：后端立即走一次通道投递（失败仍记 error，不抛错）
function resend(row) {
  ElMessageBox.confirm(`确认重发通知「${row.title}」（${row.user_name}）？`, '重发通知', { type: 'warning' })
    .then(() => request.post(`/api/admin/notifications/${row.id}/resend`, {}, { silent: true }))
    .then(() => {
      ElMessage.success('已触发重发');
      load();
    })
    .catch((err) => {
      if (err && err.message) {
        ElMessage.warning(err.message);
      }
    });
}

onMounted(load);
</script>

<style scoped>
.filter-form {
  margin-bottom: 4px;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
.muted {
  color: #5b6b7d;
  font-size: 12px;
}
</style>
