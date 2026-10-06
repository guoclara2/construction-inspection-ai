<template>
  <el-card shadow="never">
    <el-tabs v-model="activeTab">
      <!-- 工单列表 tab -->
      <el-tab-pane label="工单列表" name="list">
        <!-- 筛选区 -->
        <el-form inline class="filter-form">
          <el-form-item label="状态">
            <el-select v-model="filters.status" placeholder="全部" clearable aria-label="按状态筛选" style="width: 120px">
              <el-option v-for="(label, value) in STATUS" :key="value" :label="label" :value="value" />
            </el-select>
          </el-form-item>
          <el-form-item label="责任人">
            <el-select v-model="filters.assignee_id" placeholder="全部" clearable aria-label="按责任人筛选" style="width: 140px">
              <el-option v-for="a in assignees" :key="a.user_id" :label="a.name" :value="a.user_id" />
            </el-select>
          </el-form-item>
          <el-form-item label="仅看超期">
            <el-switch v-model="filters.overdue" aria-label="仅看超期工单" />
          </el-form-item>
          <el-form-item label="即将超期">
            <el-switch v-model="filters.warn" aria-label="仅看即将超期工单" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="search">查询</el-button>
          </el-form-item>
        </el-form>

        <!-- 批量催办（P06 §2.3.2）：多选未闭环工单后可一键批量催办（≤50） -->
        <div class="batch-bar">
          <el-button type="warning" :disabled="!selection.length" :loading="batchUrgeLoading"
            @click="urgeBatch">批量催办{{ selection.length ? `（${selection.length}）` : '' }}</el-button>
          <span class="batch-hint">已选 {{ selection.length }} 条（仅未闭环单可选）</span>
        </div>

        <!-- 工单表格：超期行整行红底；P2-3 小屏适配——轮次并入检查项、责任人←发起人合并、
             创建/截止时间合并为两行，核心列（工单号/检查项/状态/责任人/时间）在 656px 下无横向滚动 -->
        <el-table :data="orders" v-loading="loading" border stripe :row-class-name="rowClassName"
          table-layout="auto" @selection-change="onSelectionChange" @sort-change="onSortChange">
          <el-table-column type="selection" width="44" :selectable="selectableRow" />
          <el-table-column prop="order_no" label="工单号" min-width="110" show-overflow-tooltip />
          <el-table-column label="检查项" min-width="130" show-overflow-tooltip>
            <template #default="{ row }">
              {{ row.item_name }}
              <el-tag size="small" effect="plain" class="round-badge">第{{ row.round }}轮</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" min-width="78">
            <template #default="{ row }">
              <el-tag :type="STATUS_TAG[row.status]">{{ STATUS[row.status] || row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="责任人 ← 发起人" min-width="110" show-overflow-tooltip>
            <template #default="{ row }">{{ row.assignee_name }} ← {{ row.inspector_name }}</template>
          </el-table-column>
          <el-table-column label="创建 / 截止" min-width="115">
            <template #default="{ row }">
              <div class="cell-two-line">
                <div>{{ fmtTime(row.created_at) }}</div>
                <div :class="{ 'deadline-overdue': row.overdue }">{{ fmtTime(row.deadline) }}</div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="超期时长" min-width="92" sortable="custom" :sort-orders="['descending', 'ascending']">
            <template #default="{ row }">
              <span v-if="row.overdue" class="overdue-hours">超 {{ row.overdue_hours }} 小时</span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="110" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openDetail(row)">详情</el-button>
              <el-button v-if="row.status !== 'closed' && row.status !== 'cancelled'" link type="warning" @click="urge(row)">催办</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="pager">
          <el-pagination v-model:current-page="page" :page-size="pageSize" :total="total"
            layout="total, prev, pager, next" @current-change="loadOrders" />
        </div>
      </el-tab-pane>

      <!-- 统计看板 tab（首次切入再挂载，保证图表容器有尺寸） -->
      <el-tab-pane label="统计看板" name="dashboard">
        <Dashboard v-if="dashboardVisited" @urge="urge" @view-all-overdue="viewAllOverdue" />
      </el-tab-pane>
    </el-tabs>

    <!-- 工单详情抽屉：只读监督视角 -->
    <el-drawer v-model="drawer.visible" :title="'工单详情 · ' + (detail.order_no || '')" size="60%">
      <template v-if="detail.id">
        <OrderOperations :order="detail" @changed="async()=>{detail=await request.get('/api/orders/'+detail.id)}" />
        <!-- 基本信息条 -->
        <el-descriptions :column="3" border size="small" class="mb16">
          <el-descriptions-item label="工单号">{{ detail.order_no }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="STATUS_TAG[detail.status]">{{ STATUS[detail.status] }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="轮次">第 {{ detail.round }} 轮</el-descriptions-item>
          <el-descriptions-item label="责任人">{{ detail.assignee_name }}</el-descriptions-item>
          <el-descriptions-item label="发起人">{{ detail.inspector_name }}</el-descriptions-item>
          <el-descriptions-item label="整改时限">
            <span :class="{ 'deadline-overdue': detail.overdue }">{{ fmtTime(detail.deadline) }}</span>
            <span v-if="detail.overdue" class="overdue-hours">（超 {{ detail.overdue_hours }} 小时）</span>
          </el-descriptions-item>
        </el-descriptions>

        <!-- 三段问题卡片 -->
        <el-card shadow="never" class="mb16">
          <template #header>问题定位</template>
          <div class="section-body">{{ detail.problem_location }}</div>
        </el-card>
        <el-card shadow="never" class="mb16">
          <template #header>整改要求</template>
          <div class="section-body">{{ detail.rectify_requirement }}</div>
        </el-card>
        <el-card shadow="never" class="mb16">
          <template #header>规范依据</template>
          <div class="section-body">{{ detail.basis }}</div>
        </el-card>

        <!-- 原始照片 + AI 判别摘要 -->
        <el-card shadow="never" class="mb16">
          <template #header>原始巡查记录</template>
          <template v-if="detail.record">
            <div class="record-name">{{ detail.record.item_name }}
              <el-tag size="small" effect="plain" class="ml8">
                {{ CATEGORY[detail.record.defect_category] || detail.record.defect_category }}
              </el-tag>
            </div>
            <!-- P1-4：存证异常照片红框 + 右上角标签，点击展开具体异常项 -->
            <div v-if="recordPhotoUrl" class="photo-box" :class="{ 'photo-suspicious': recordPhotoMeta && recordPhotoMeta.suspicious }">
              <el-image :src="recordPhotoUrl"
                :preview-src-list="[recordPhotoUrl]" fit="cover" class="record-photo" />
              <div v-if="recordPhotoMeta && recordPhotoMeta.suspicious" class="photo-warn-badge"
                @click.stop="evidenceExpanded.record = !evidenceExpanded.record">存证异常</div>
            </div>
            <div v-if="recordPhotoMeta" class="evidence-line">
              <span class="evidence-text">拍摄：{{ recordPhotoMeta.exifTime }}｜{{ recordPhotoMeta.coord }}｜{{ recordPhotoMeta.sourceLabel }}</span>
            </div>
            <div v-if="recordPhotoMeta && recordPhotoMeta.suspicious && evidenceExpanded.record" class="evidence-issues">
              <div v-for="(issue, idx) in evidenceIssues(recordPhoto)" :key="idx" class="evidence-issue-item">{{ issue }}</div>
            </div>
            <div v-if="aiSummary" class="ai-summary">
              <b>AI 判别：</b>{{ aiSummary }}
            </div>
          </template>
        </el-card>

        <!-- 整改反馈 -->
        <el-card shadow="never" class="mb16">
          <template #header>整改反馈</template>
          <template v-if="feedbackPhotoUrl || detail.feedback_text">
            <div v-if="feedbackPhotoUrl" class="photo-box" :class="{ 'photo-suspicious': feedbackPhotoMeta && feedbackPhotoMeta.suspicious }">
              <el-image :src="feedbackPhotoUrl"
                :preview-src-list="[feedbackPhotoUrl]" fit="cover" class="record-photo" />
              <div v-if="feedbackPhotoMeta && feedbackPhotoMeta.suspicious" class="photo-warn-badge"
                @click.stop="evidenceExpanded.feedback = !evidenceExpanded.feedback">存证异常</div>
            </div>
            <div v-if="feedbackPhotoMeta" class="evidence-line">
              <span class="evidence-text">拍摄：{{ feedbackPhotoMeta.exifTime }}｜{{ feedbackPhotoMeta.coord }}｜{{ feedbackPhotoMeta.sourceLabel }}</span>
            </div>
            <div v-if="feedbackPhotoMeta && feedbackPhotoMeta.suspicious && evidenceExpanded.feedback" class="evidence-issues">
              <div v-for="(issue, idx) in evidenceIssues(feedbackPhoto)" :key="idx" class="evidence-issue-item">{{ issue }}</div>
            </div>
            <div v-if="detail.feedback_text" class="section-body">{{ detail.feedback_text }}</div>
          </template>
          <div v-else class="muted">责任人尚未提交整改反馈</div>
        </el-card>

        <!-- 全流程时间线 -->
        <el-card shadow="never" class="mb16">
          <template #header>流转日志</template>
          <el-timeline>
            <el-timeline-item v-for="lg in detail.logs" :key="lg.id" :timestamp="fmtTime(lg.created_at)"
              placement="top">
              <div class="tl-action">{{ ACTION_CN[lg.action] || lg.action }} · {{ lg.operator_name }}</div>
              <div v-if="lg.remark" class="tl-remark">{{ lg.remark }}</div>
            </el-timeline-item>
          </el-timeline>
        </el-card>

        <!-- 抽屉内催办 -->
        <div v-if="detail.status !== 'closed' && detail.status !== 'cancelled'" class="drawer-footer">
          <el-button type="warning" @click="urge(detail)">催办</el-button>
        </div>
      </template>
    </el-drawer>
  </el-card>
</template>

<script setup>
import OrderOperations from "../../components/OrderOperations.vue";
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import request, { signedImageUrl } from '../../utils/request';
import { STATUS, STATUS_TAG, CATEGORY, fmtTime } from '../../utils/dict';
import Dashboard from '../dashboard/index.vue';

// 日志动作中文（总纲 4.6 枚举）
const ACTION_CN = {
  create: '创建工单',
  accept: '接收',
  start: '开始整改',
  feedback: '提交反馈',
  review_pass: '审核通过',
  review_reject: '审核打回',
  urge: '催办',
};

const activeTab = ref('list');

// 统计看板：首次切入该 tab 再挂载组件（保证 ECharts 容器可见时 init）
const dashboardVisited = ref(false);
watch(activeTab, (v) => {
  if (v === 'dashboard') dashboardVisited.value = true;
});

// ---------- 列表 ----------
const orders = ref([]);
const page = ref(1);
const pageSize = 20;
const total = ref(0);
const loading = ref(false);
const filters = reactive({ status: '', assignee_id: null, overdue: false, warn: false });
const assignees = ref([]);
// 超期时长排序（P06 §2.5.1）：后端 sort=overdue_desc/overdue_asc（超期单按 deadline 反序，非超期单排后）
const sortBy = ref('');

function loadOrders() {
  loading.value = true;
  const params = { page: page.value, page_size: pageSize };
  if (filters.status) params.status = filters.status;
  if (filters.assignee_id) params.assignee_id = filters.assignee_id;
  if (filters.overdue) params.overdue = true;
  if (filters.warn) params.warn = true;
  if (sortBy.value) params.sort = sortBy.value;
  request.get('/api/admin/orders', { params })
    .then((data) => {
      orders.value = data.list || [];
      total.value = data.total || 0;
    })
    .catch(() => {})
    .finally(() => {
      loading.value = false;
    });
}

// 表头排序回调：超期时长列 descending→overdue_desc / ascending→overdue_asc / 取消→空
function onSortChange({ prop, order }) {
  if (order === 'descending') {
    sortBy.value = 'overdue_desc';
  } else if (order === 'ascending') {
    sortBy.value = 'overdue_asc';
  } else {
    sortBy.value = '';
  }
  page.value = 1;
  loadOrders();
}

// ---------- 批量催办（P06 §2.3.2） ----------
const selection = ref([]);
const batchUrgeLoading = ref(false);

// 仅未闭环/未作废单可勾选（闭环单催办无意义）
function selectableRow(row) {
  return row.status !== 'closed' && row.status !== 'cancelled';
}

function onSelectionChange(rows) {
  selection.value = rows || [];
}

function urgeBatch() {
  const ids = selection.value.map((r) => r.id);
  if (!ids.length) {
    return;
  }
  if (ids.length > 50) {
    ElMessage.warning('单次批量催办最多 50 条，请减少选择');
    return;
  }
  ElMessageBox.confirm(`确认对选中的 ${ids.length} 张工单批量催办？`, '批量催办', { type: 'warning' })
    .then(() => {
      batchUrgeLoading.value = true;
      return request.post('/api/admin/orders/urge-batch', { order_ids: ids }, { silent: true });
    })
    .then((data) => {
      const urged = (data && data.urged) || [];
      const skipped = (data && data.skipped) || [];
      if (!skipped.length) {
        ElMessage.success(`已催办 ${urged.length} 张工单`);
      } else {
        const reasons = skipped.map((s) => `#${s.order_id}：${s.reason}`).join('；');
        ElMessage.warning(`已催办 ${urged.length} 张，跳过 ${skipped.length} 张（${reasons}）`);
      }
      loadOrders();
    })
    .catch((err) => {
      if (err && err.message) {
        ElMessage.warning(err.message);
      }
    })
    .finally(() => {
      batchUrgeLoading.value = false;
    });
}

function search() {
  page.value = 1;
  loadOrders();
}

// 看板「查看全部超期」：切回列表 tab 并开启仅看超期（完整清单走数据库分页）
function viewAllOverdue() {
  activeTab.value = 'list';
  filters.overdue = true;
  page.value = 1;
  loadOrders();
}

function loadAssignees() {
  // 责任人下拉：复用指派选项接口（rectifier + 未闭环数）
  request.get('/api/orders/assignee-options').then((data) => {
    assignees.value = data || [];
  }).catch(() => {});
}

// 超期行整行红色背景
function rowClassName({ row }) {
  return row.overdue ? 'overdue-row' : '';
}

// ---------- 催办 ----------
function urge(row) {
  ElMessageBox.confirm(`确认对工单 ${row.order_no} 进行催办？`, '催办', { type: 'warning' })
    // 乐观锁版本回传（P05 §2.6）：他人已更新 → 后端 1014，warning 提示刷新
    .then(() => request.post('/api/orders/' + row.id + '/urge',
      { expected_version: row.version }, { silent: true }))
    .then(() => {
      ElMessage.success('催办成功，已通知责任人');
    })
    .catch((err) => {
      if (err && err.message) {
        // 含 429 限流 / 1014 版本冲突提示，统一 warning 提示
        ElMessage.warning(err.message);
      }
    });
}

// ---------- 详情抽屉 ----------
const drawer = reactive({ visible: false });
const detail = ref({});

const aiSummary = computed(() => {
  const ai = detail.value.record && detail.value.record.ai_result;
  if (!ai) return '';
  const verdict = ai.has_defect === null || ai.confidence < 0.6 ? '无法判断，请人工复核' : ai.has_defect === true ? '存在异常' : ai.has_defect === false ? '未发现异常' : '';
  return [verdict, ai.defect_desc].filter(Boolean).join('——');
});

// 照片为附件对象（P04）：url 经 /signed-url 换取短时效链接访问（P0-3，令牌不进 URL）；
// 存证信息（拍摄时间/坐标/来源/异常）
const recordPhoto = computed(() => (detail.value.record && detail.value.record.photo) || null);
const recordPhotoUrl = ref('');
const recordPhotoMeta = computed(() => evidenceMeta(recordPhoto.value));
const feedbackPhoto = computed(() => (detail.value.feedback_photos || [])[0] || null);
const feedbackPhotoUrl = ref('');
const feedbackPhotoMeta = computed(() => evidenceMeta(feedbackPhoto.value));
// P1-4：存证异常明细展开状态（打开详情时重置）
const evidenceExpanded = reactive({ record: false, feedback: false });

function evidenceMeta(att) {
  if (!att) return null;
  const hasGps = att.gps_lat !== null && att.gps_lat !== undefined
    && att.gps_lng !== null && att.gps_lng !== undefined;
  return {
    exifTime: att.exif_time || '无拍摄时间',
    coord: hasGps ? `${att.gps_lat}, ${att.gps_lng}` : '无定位',
    sourceLabel: att.source === 'camera' ? '现场拍摄' : (att.source === 'album' ? '相册选取' : '来源未知'),
    suspicious: att.suspicious === true,
  };
}

// P1-4：存证异常具体项（纯前端按既有字段推导；口径与后端上传时判定一致）
function evidenceIssues(att) {
  if (!att) return [];
  if (att.evidence && Array.isArray(att.evidence.reasons)) return att.evidence.reasons;
  const issues = [];
  if (!att.exif_time) {
    issues.push('无拍摄时间（照片未携带 EXIF 拍摄时间存证）');
  } else if (att.created_at && Math.abs(new Date(att.created_at) - new Date(att.exif_time)) > 2 * 3600 * 1000) {
    issues.push('时间偏差超限（拍摄时间与上传时间相差过大）');
  }
  const hasGps = att.gps_lat !== null && att.gps_lat !== undefined
    && att.gps_lng !== null && att.gps_lng !== undefined;
  if (!hasGps) {
    issues.push('无定位（照片未携带或未声明拍摄位置）');
  }
  if (att.source === 'album') {
    issues.push('相册选取（非现场拍摄）');
  }
  return issues.length ? issues : ['存证校验未通过（详见上传时系统判定）'];
}

// 打开详情时换取照片短时效签名 URL（P0-3）
function loadPhotoUrls() {
  recordPhotoUrl.value = '';
  feedbackPhotoUrl.value = '';
  evidenceExpanded.record = false;
  evidenceExpanded.feedback = false;
  if (recordPhoto.value) {
    signedImageUrl(recordPhoto.value.url)
      .then((u) => { recordPhotoUrl.value = u; })
      .catch(() => {});
  }
  if (feedbackPhoto.value) {
    signedImageUrl(feedbackPhoto.value.url)
      .then((u) => { feedbackPhotoUrl.value = u; })
      .catch(() => {});
  }
}

function openDetail(row) {
  request.get('/api/orders/' + row.id).then((data) => {
    detail.value = data;
    drawer.visible = true;
    loadPhotoUrls();
  }).catch(() => {});
}

onMounted(() => {
  loadOrders();
  loadAssignees();
});
</script>

<style scoped>
.filter-form {
  margin-bottom: 4px;
}
/* 批量催办操作条（P06 §2.5.1） */
.batch-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.batch-hint {
  font-size: 12px;
  color: #5b6b7d;
}
/* P2-3：合并单元格两行显示 + 检查项内轮次角标 */
.cell-two-line {
  line-height: 1.5;
  font-size: 12px;
}
.round-badge {
  margin-left: 4px;
}
.muted {
  color: #909399;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
/* 超期行整行红底 */
:deep(.el-table .overdue-row) {
  background: #fef0f0 !important;
}
:deep(.el-table .overdue-row td) {
  background: transparent !important;
}
.deadline-overdue {
  color: #e5652a;
  font-weight: 700;
}
.overdue-hours {
  font-size: 12px;
  color: #e5652a;
  font-weight: 600;
}
.mb16 {
  margin-bottom: 16px;
}
.ml8 {
  margin-left: 8px;
}
.section-body {
  font-size: 14px;
  color: #1a2634;
  line-height: 1.7;
}
.record-name {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 8px;
}
.record-photo {
  width: 320px;
  height: 200px;
  border-radius: 6px;
  margin-bottom: 8px;
}
/* P1-4：存证异常照片容器——2px 红框 + 右上角标签 */
.photo-box {
  position: relative;
  display: inline-block;
}
.photo-suspicious {
  border: 2px solid #e5652a;
  border-radius: 8px;
  padding: 2px;
}
.photo-warn-badge {
  position: absolute;
  top: 6px;
  right: 6px;
  background: #e5652a;
  color: #fff;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1;
  padding: 5px 8px;
  cursor: pointer;
  user-select: none;
}
.evidence-issues {
  background: #fdf3ef;
  border: 1px solid #e5652a;
  border-radius: 6px;
  margin-bottom: 8px;
  padding: 8px 12px;
}
.evidence-issue-item {
  color: #b04a1e;
  font-size: 12px;
  line-height: 1.8;
}
/* 照片存证信息行（P04 §2.5.5） */
.evidence-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.evidence-text {
  font-size: 12px;
  color: #5b6b7d;
}
.ai-summary {
  background: #f5f7fa;
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 13px;
  color: #5b6b7d;
  line-height: 1.7;
}
.muted {
  color: #5b6b7d;
  font-size: 14px;
}
.tl-action {
  font-size: 14px;
  font-weight: 600;
  color: #1a2634;
}
.tl-remark {
  font-size: 13px;
  color: #5b6b7d;
  margin-top: 4px;
}
.drawer-footer {
  padding: 8px 0 16px;
}
</style>
