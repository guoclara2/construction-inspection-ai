<template>
  <div class="reports-page">
    <!-- 页头 -->
    <div class="page-header">
      <div>
        <div class="page-title">报表与导出</div>
        <div class="page-desc">面向政府抽查、竣工验收、集团汇报，导出可盖章留痕的巡查 / 整改报告（PDF）</div>
      </div>
    </div>

    <el-card shadow="never">
      <el-tabs v-model="activeTab">
        <!-- 巡查报告 -->
        <el-tab-pane label="巡查报告" name="record">
          <el-form inline class="filter-form">
            <el-form-item label="判定结论">
              <el-select v-model="recordFilters.verdict" placeholder="全部" clearable style="width: 140px"
                aria-label="按结论筛选巡查记录" @change="loadRecords">
                <el-option v-for="(label, value) in VERDICT" :key="value" :label="label" :value="value" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="loadRecords">查询</el-button>
            </el-form-item>
          </el-form>

          <el-table :data="records" v-loading="recordLoading" border stripe>
            <el-table-column prop="id" label="编号" width="80" />
            <el-table-column prop="item_name" label="检查项" min-width="180" show-overflow-tooltip />
            <el-table-column label="判定结论" width="100">
              <template #default="{ row }">
                <el-tag v-if="row.human_verdict" :type="row.human_verdict === 'abnormal' ? 'danger' : 'success'">
                  {{ VERDICT[row.human_verdict] }}
                </el-tag>
                <el-tag v-else type="info">待确认</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="巡查时间" min-width="160">
              <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" :loading="exporting === 'record-' + row.id"
                  @click="exportRecord(row)">导出 PDF</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="pager">
            <el-pagination v-model:current-page="recordPage" :page-size="recordPageSize" :total="recordTotal"
              layout="total, prev, pager, next" @current-change="loadRecords" />
          </div>
        </el-tab-pane>

        <!-- 整改报告 -->
        <el-tab-pane label="整改报告" name="order">
          <el-form inline class="filter-form">
            <el-form-item label="状态">
              <el-select v-model="orderFilters.status" placeholder="全部" clearable style="width: 140px"
                aria-label="按状态筛选工单" @change="loadOrders">
                <el-option v-for="(label, value) in STATUS" :key="value" :label="label" :value="value" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="loadOrders">查询</el-button>
            </el-form-item>
          </el-form>

          <el-table :data="orders" v-loading="orderLoading" border stripe>
            <el-table-column prop="order_no" label="工单号" min-width="160" show-overflow-tooltip />
            <el-table-column prop="item_name" label="检查项" min-width="160" show-overflow-tooltip />
            <el-table-column label="状态" width="92">
              <template #default="{ row }">
                <el-tag :type="STATUS_TAG[row.status]">{{ STATUS[row.status] || row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="责任 / 发起" min-width="140" show-overflow-tooltip>
              <template #default="{ row }">{{ row.assignee_name }} ← {{ row.inspector_name }}</template>
            </el-table-column>
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" :loading="exporting === 'order-' + row.id"
                  @click="exportOrder(row)">导出 PDF</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="pager">
            <el-pagination v-model:current-page="orderPage" :page-size="orderPageSize" :total="orderTotal"
              layout="total, prev, pager, next" @current-change="loadOrders" />
          </div>
        </el-tab-pane>

        <!-- 汇总报告 -->
        <el-tab-pane label="项目汇总报告" name="summary">
          <el-form inline class="filter-form">
            <el-form-item label="统计周期">
              <el-date-picker v-model="summaryRange" type="daterange" range-separator="至"
                start-placeholder="开始日期" end-placeholder="结束日期" value-format="YYYY-MM-DD"
                :clearable="false" style="width: 280px" aria-label="选择统计周期" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="exporting === 'summary'" @click="exportSummary">
                生成汇总报告
              </el-button>
            </el-form-item>
          </el-form>

          <el-alert type="info" :closable="false" show-icon class="summary-tip">
            <template #title>
              汇总报告按当前项目的巡查记录与整改工单统计，供集团汇报、竣工验收使用；导出后请在打印时加盖实体公章存档。
            </template>
          </el-alert>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import request from '../../utils/request';
import { STATUS, STATUS_TAG, VERDICT, fmtTime } from '../../utils/dict';

const activeTab = ref('record');
const exporting = ref('');

// —— 巡查报告 ——
const records = ref([]);
const recordTotal = ref(0);
const recordPage = ref(1);
const recordPageSize = 20;
const recordLoading = ref(false);
const recordFilters = ref({ verdict: '' });

// —— 整改报告 ——
const orders = ref([]);
const orderTotal = ref(0);
const orderPage = ref(1);
const orderPageSize = 20;
const orderLoading = ref(false);
const orderFilters = ref({ status: '' });

// —— 汇总报告 ——
const summaryRange = ref([recentDays(30), today()]);

function today() {
  const d = new Date();
  return formatDate(d);
}
function recentDays(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return formatDate(d);
}
function formatDate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

async function loadRecords() {
  recordLoading.value = true;
  try {
    const data = await request.get('/api/inspection-records', {
      params: {
        verdict: recordFilters.value.verdict || undefined,
        page: recordPage.value,
        page_size: recordPageSize,
      },
    });
    records.value = data.list || [];
    recordTotal.value = data.total || 0;
  } finally {
    recordLoading.value = false;
  }
}

async function loadOrders() {
  orderLoading.value = true;
  try {
    const data = await request.get('/api/orders', {
      params: {
        view: 'all',
        status: orderFilters.value.status || undefined,
        page: orderPage.value,
        page_size: orderPageSize,
      },
    });
    orders.value = data.list || [];
    orderTotal.value = data.total || 0;
  } finally {
    orderLoading.value = false;
  }
}

function exportRecord(row) {
  downloadPdf(`/api/reports/record/${row.id}`, `巡查报告_${row.id}.pdf`, 'record-' + row.id);
}
function exportOrder(row) {
  downloadPdf(`/api/reports/order/${row.id}`, `整改报告_${row.order_no}.pdf`, 'order-' + row.id);
}
function exportSummary() {
  const [start, end] = summaryRange.value || [];
  if (!start || !end) {
    ElMessage.error('请选择统计周期');
    return;
  }
  downloadPdf(
    `/api/reports/summary?start=${start}&end=${end}`,
    `项目巡查汇总_${start}_${end}.pdf`,
    'summary',
  );
}

async function downloadPdf(url, filename, key) {
  exporting.value = key;
  try {
    const blob = await request.get(url, { responseType: 'blob', silent: true });
    if (!blob || !blob.type || !String(blob.type).includes('pdf')) {
      let msg = '导出失败，请稍后重试';
      try {
        if (blob) {
          const text = await blob.text();
          const parsed = JSON.parse(text);
          if (parsed && parsed.msg) msg = parsed.msg;
          else if (parsed && parsed.detail) msg = typeof parsed.detail === 'string' ? parsed.detail : (parsed.detail.msg || msg);
        }
      } catch (e) { /* 非 JSON 错误体 */ }
      ElMessage.error(msg);
      return;
    }
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = objectUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(objectUrl);
    ElMessage.success('已生成并开始下载');
  } catch (e) {
    const msg = (e && e.message && !String(e.message).includes('网络异常'))
      ? e.message
      : '导出失败：接口不可用，请确认后端服务已启动';
    ElMessage.error(msg);
  } finally {
    exporting.value = '';
  }
}

onMounted(() => {
  loadRecords();
  loadOrders();
});
</script>

<style scoped>
.reports-page .page-header {
  margin-bottom: 16px;
}
.page-title {
  font-size: 20px;
  font-weight: 700;
  color: #1a2634;
}
.page-desc {
  margin-top: 6px;
  font-size: 13px;
  color: #5b6b7d;
}
.filter-form {
  margin-bottom: 8px;
}
.filter-form :deep(.el-form-item) {
  margin-bottom: 12px;
}
.pager {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
.summary-tip {
  max-width: 640px;
}
</style>