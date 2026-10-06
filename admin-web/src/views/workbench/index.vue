<template>
  <div v-loading="loading" class="workbench">
    <!-- 指标卡行（4 卡等宽 grid，窄屏两列）；数据复用 /api/admin/dashboard（range=all） -->
    <div class="metrics-row">
      <el-card shadow="never" class="metric-card">
        <div class="metric-value">{{ metrics.inspect_count }}</div>
        <div class="metric-label">巡查总次数</div>
        <div class="metric-hint">项目启动以来全部巡查记录</div>
      </el-card>
      <el-card shadow="never" class="metric-card">
        <div class="metric-value">{{ metrics.abnormal_count }}</div>
        <div class="metric-label">异常确认数</div>
        <div class="metric-hint">人工确认异常的记录数</div>
      </el-card>
      <el-card shadow="never" class="metric-card metric-card-sla">
        <div v-if="overdueTotal > 0" class="sla-overdue-badge">含 {{ overdueTotal }} 单超期未闭环</div>
        <div class="metric-value" :class="{ 'on-time-low': onTimeLow }">{{ metrics.sla_on_time_rate }}%</div>
        <div class="metric-label">按期完成率</div>
        <div class="metric-hint">{{ slaHint }}</div>
      </el-card>
      <el-card shadow="never" class="metric-card">
        <div class="metric-value" :class="{ 'overdue-num': overdueTotal > 0 }">{{ overdueTotal }}</div>
        <div class="metric-label">超期未闭环</div>
        <div class="metric-hint">已超 SLA 时限且尚未闭环的工单数</div>
      </el-card>
    </div>

    <!-- 双栏：超期清单 top5 + 最近通知 5 条；窄屏纵向堆叠 -->
    <el-row :gutter="16" class="lists-row">
      <el-col :xs="24" :md="14">
        <el-card shadow="never" class="list-card">
          <template #header>
            <div class="card-head">
              <span>超期工单 Top 5</span>
              <el-button link type="primary" @click="goSupervise">查看全部</el-button>
            </div>
          </template>
          <el-table v-if="overdueList.length" :data="overdueList" border stripe size="small">
            <el-table-column prop="order_no" label="工单号" min-width="110" show-overflow-tooltip />
            <el-table-column prop="item_name" label="检查项" min-width="130" show-overflow-tooltip />
            <el-table-column prop="assignee_name" label="责任人" min-width="80" show-overflow-tooltip />
            <el-table-column label="超期时长" min-width="95">
              <template #default="{ row }">
                <span class="overdue-text">超 {{ row.overdue_hours }} 小时</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" min-width="76">
              <template #default="{ row }">
                <el-tag size="small" :type="STATUS_TAG[row.status]">{{ STATUS[row.status] || row.status }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="暂无超期工单" :image-size="70" />
        </el-card>
      </el-col>
      <el-col :xs="24" :md="10">
        <el-card shadow="never" class="list-card">
          <template #header>
            <div class="card-head">
              <span>最近通知</span>
              <el-button link type="primary" @click="goNotifications">查看全部</el-button>
            </div>
          </template>
          <ul v-if="notifications.length" class="notice-list">
            <li v-for="n in notifications" :key="n.id" class="notice-item">
              <div class="notice-title-row">
                <span class="notice-title">{{ n.title }}</span>
                <el-tag size="small" :type="NOTIFY_STATUS_TAG[n.status] || 'info'">
                  {{ NOTIFY_STATUS[n.status] || n.status }}
                </el-tag>
              </div>
              <div class="notice-meta muted">{{ n.user_name }} · {{ fmtTime(n.created_at) }}</div>
            </li>
          </ul>
          <el-empty v-else description="暂无通知" :image-size="70" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import request from '../../utils/request';
import { STATUS, STATUS_TAG, fmtTime } from '../../utils/dict';

const router = useRouter();

const loading = ref(false);
const metrics = reactive({ inspect_count: 0, abnormal_count: 0, sla_on_time_rate: 0 });
const slaHint = ref('按期完成率=已闭环工单中按期完成的比例');
const overdueList = ref([]);
const overdueTotal = ref(0);
const notifications = ref([]);

// 与通知记录页一致的状态映射
const NOTIFY_STATUS = { pending: '待发送', sent: '已发送', failed: '失败', skipped: '已跳过' };
const NOTIFY_STATUS_TAG = { pending: 'info', sent: 'success', failed: 'danger', skipped: 'warning' };

const onTimeLow = computed(() =>
  metrics.sla_on_time_rate !== null && metrics.sla_on_time_rate !== undefined
  && Number(metrics.sla_on_time_rate) < 80,
);

function load() {
  loading.value = true;
  const p1 = request.get('/api/admin/dashboard', { params: { range: 'all' } })
    .then((data) => {
      Object.assign(metrics, data.metrics || {});
      slaHint.value = (data.metrics && data.metrics.sla_on_time_hint) || slaHint.value;
      overdueList.value = (data.overdue_list || []).slice(0, 5);
      overdueTotal.value = data.overdue_total || 0;
    })
    .catch(() => {});
  const p2 = request.get('/api/admin/notifications', { params: { page: 1, page_size: 5 } })
    .then((data) => {
      notifications.value = data.list || [];
    })
    .catch(() => {});
  Promise.all([p1, p2]).finally(() => {
    loading.value = false;
  });
}

function goSupervise() {
  router.push('/supervise');
}

function goNotifications() {
  router.push('/notifications');
}

onMounted(load);
</script>

<style scoped>
.metrics-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}
@media (max-width: 992px) {
  .metrics-row {
    grid-template-columns: repeat(2, 1fr);
  }
}
.metric-card {
  text-align: center;
}
.metric-hint {
  font-size: 12px;
  color: #98a3b3;
  line-height: 1.5;
  margin-top: 6px;
  min-height: 36px;
}
.metric-card-sla {
  position: relative;
}
.sla-overdue-badge {
  position: absolute;
  top: 8px;
  right: 8px;
  background: #fdeaea;
  color: #e5652a;
  border: 1px solid #e5652a;
  border-radius: 4px;
  font-size: 12px;
  padding: 2px 6px;
  white-space: nowrap;
}
.on-time-low,
.overdue-num {
  color: #e5652a;
}
.metric-value {
  font-size: 28px;
  font-weight: 700;
  color: #1a2634;
  line-height: 1.4;
}
.metric-label {
  font-size: 13px;
  color: #5b6b7d;
  margin-top: 4px;
}
.lists-row {
  margin-bottom: 16px;
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.notice-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.notice-item {
  padding: 10px 4px;
  border-bottom: 1px solid #f0f2f5;
}
.notice-item:last-child {
  border-bottom: none;
}
.notice-title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.notice-title {
  font-size: 13px;
  color: #1a2634;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.notice-meta {
  font-size: 12px;
  margin-top: 4px;
}
.muted {
  color: #909399;
}
.overdue-text {
  color: #e5652a;
  font-weight: 700;
}
</style>
