<template>
  <div v-loading="loading" class="dashboard">
    <!-- 页头：时间范围切换 -->
    <div class="range-bar">
      <el-radio-group v-model="range" @change="load">
        <el-radio-button value="7d">近 7 天</el-radio-button>
        <el-radio-button value="30d">近 30 天</el-radio-button>
        <el-radio-button value="all">全部</el-radio-button>
      </el-radio-group>
    </div>

    <!-- 指标卡行（5 卡等宽 grid，窄屏两列）；每卡附口径说明（P1-3） -->
    <div class="metrics-row">
      <el-card shadow="never" class="metric-card">
        <div class="metric-value">{{ metrics.inspect_count }}</div>
        <div class="metric-label">巡查总次数</div>
        <div class="metric-hint">所选时间范围内的巡查记录数</div>
      </el-card>
      <el-card shadow="never" class="metric-card">
        <div class="metric-value">{{ metrics.abnormal_count }}</div>
        <div class="metric-label">异常确认数</div>
        <div class="metric-hint">所选时间范围内人工确认异常的记录数</div>
      </el-card>
      <el-card shadow="never" class="metric-card">
        <div class="metric-value">{{ metrics.ai_consistent_rate }}%</div>
        <div class="metric-label">AI 判别一致率</div>
        <div class="metric-hint">人工确认与 AI 判别结论一致的比例（按已确认记录回流统计）</div>
      </el-card>
      <el-card shadow="never" class="metric-card">
        <div class="metric-value">{{ avgText }}</div>
        <div class="metric-label">平均整改时长</div>
        <div class="metric-hint">已闭环工单从创建到闭环的平均耗时</div>
      </el-card>
      <el-card shadow="never" class="metric-card metric-card-sla">
        <div v-if="overdueTotal > 0" class="sla-overdue-badge">含 {{ overdueTotal }} 单超期未闭环</div>
        <div class="metric-value" :class="{ 'on-time-low': onTimeLow }">{{ metrics.sla_on_time_rate }}%</div>
        <div class="metric-label">按期完成率</div>
        <div class="metric-hint">{{ slaHint }}</div>
      </el-card>
    </div>

    <!-- 图表区：两列布局，窄屏纵向堆叠 -->
    <el-row :gutter="16">
      <el-col :xs="24" :sm="12">
        <el-card shadow="never">
          <template #header>问题类型分布</template>
          <div v-show="hasPie" ref="pieRef" class="chart" />
          <el-empty v-if="!hasPie" description="暂无数据" :image-size="80" />
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12">
        <el-card shadow="never">
          <template #header>工单状态漏斗</template>
          <div v-show="hasFunnel" ref="funnelRef" class="chart" />
          <el-empty v-if="!hasFunnel" description="暂无数据" :image-size="80" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 超期工单清单（P05 §2.4：后端只返回最紧急前 20 条 + overdue_total，完整清单走列表页筛选） -->
    <el-card shadow="never" class="overdue-card">
      <template #header>
        <span>超期工单清单</span>
        <el-tag v-if="overdueTotal > 0" type="danger" size="small" class="overdue-total-tag">
          共 {{ overdueTotal }} 条
        </el-tag>
      </template>
      <el-table v-if="overdueList.length" :data="overdueList" border stripe>
        <el-table-column prop="order_no" label="工单号" min-width="130" />
        <el-table-column prop="item_name" label="检查项" min-width="180" show-overflow-tooltip />
        <el-table-column prop="assignee_name" label="责任人" min-width="90" />
        <el-table-column label="超期时长" min-width="110">
          <template #default="{ row }">
            <span class="overdue-text">超 {{ row.overdue_hours }} 小时</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" min-width="90">
          <template #default="{ row }">
            <el-tag :type="STATUS_TAG[row.status]">{{ STATUS[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button link type="warning" @click="emit('urge', row)">催办</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无数据" :image-size="80" />
      <div v-if="overdueTotal > overdueList.length" class="overdue-more">
        仅显示最紧急前 {{ overdueList.length }} 条，共 {{ overdueTotal }} 条
        <el-button link type="primary" @click="emit('view-all-overdue')">查看全部</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue';
import * as echarts from 'echarts/core';
import { PieChart, FunnelChart } from 'echarts/charts';
import { TooltipComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
echarts.use([PieChart, FunnelChart, TooltipComponent, LegendComponent, CanvasRenderer]);
import request from '../../utils/request';
import { STATUS, STATUS_TAG } from '../../utils/dict';

const emit = defineEmits(['urge', 'view-all-overdue']);

// ---------- 数据 ----------
const range = ref('7d');
const loading = ref(false);
const metrics = reactive({ inspect_count: 0, abnormal_count: 0, ai_consistent_rate: 0, avg_rectify_hours: null, sla_on_time_rate: 0 });
// P1-3：按期完成率口径说明由后端生成（含超期未闭环单数，明确两数字关系）
const slaHint = ref('按期完成率=已闭环工单中按期完成的比例');
const problemPie = ref([]);
const statusFunnel = ref([]);
const overdueList = ref([]);
const overdueTotal = ref(0);

const hasPie = computed(() => problemPie.value.some((d) => d.value > 0));
const hasFunnel = computed(() => statusFunnel.value.some((d) => d.value > 0));
const avgText = computed(() =>
  metrics.avg_rectify_hours > 0 ? `${metrics.avg_rectify_hours} 小时` : '—',
);
// 按期完成率偏低预警（P06 §2.5.3）：< 80% 标红提醒治理
const onTimeLow = computed(() =>
  metrics.sla_on_time_rate !== null && metrics.sla_on_time_rate !== undefined
  && Number(metrics.sla_on_time_rate) < 80,
);

function load() {
  loading.value = true;
  request.get('/api/admin/dashboard', { params: { range: range.value } })
    .then((data) => {
      problemPie.value = data.problem_pie || [];
      statusFunnel.value = data.status_funnel || [];
      Object.assign(metrics, data.metrics || {});
      slaHint.value = (data.metrics && data.metrics.sla_on_time_hint) || slaHint.value;
      overdueList.value = data.overdue_list || [];
      overdueTotal.value = data.overdue_total || 0;
      nextTick(renderCharts);
    })
    .catch(() => {
      // request 拦截器已统一 ElMessage.error
    })
    .finally(() => {
      loading.value = false;
    });
}

// ---------- 图表 ----------
const pieRef = ref(null);
const funnelRef = ref(null);
let pieChart = null;
let funnelChart = null;

// 色板：主色系衍生（总纲第 8 章）
const PIE_COLORS = ['#1E5FD0', '#E5652A', '#E6A23C', '#2E7D3A', '#9C27B0'];
// 漏斗按状态色：pending/accepted/processing/review/closed
const FUNNEL_COLORS = ['#909399', '#1E5FD0', '#E6A23C', '#9C27B0', '#2E7D3A'];

function renderCharts() {
  if (hasPie.value && pieRef.value) {
    if (!pieChart) pieChart = echarts.init(pieRef.value);
    pieChart.setOption({
      color: PIE_COLORS,
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { bottom: 0, icon: 'circle' },
      series: [
        {
          type: 'pie',
          radius: ['40%', '65%'],
          center: ['50%', '44%'],
          itemStyle: { borderColor: '#fff', borderWidth: 2 },
          label: { formatter: '{b} {c}' },
          data: problemPie.value,
        },
      ],
    });
    pieChart.resize();
  }
  if (hasFunnel.value && funnelRef.value) {
    if (!funnelChart) funnelChart = echarts.init(funnelRef.value);
    funnelChart.setOption({
      color: FUNNEL_COLORS,
      tooltip: { trigger: 'item', formatter: '{b}: {c}' },
      series: [
        {
          type: 'funnel',
          left: '10%',
          width: '80%',
          top: 10,
          bottom: 20,
          sort: 'none', // 保持 pending→closed 契约顺序
          gap: 4,
          label: { show: true, position: 'inside', formatter: '{b} {c}' },
          data: statusFunnel.value,
        },
      ],
    });
    funnelChart.resize();
  }
}

function resizeCharts() {
  if (pieChart) pieChart.resize();
  if (funnelChart) funnelChart.resize();
}

onMounted(() => {
  window.addEventListener('resize', resizeCharts);
  load();
});

onUnmounted(() => {
  window.removeEventListener('resize', resizeCharts);
  if (pieChart) {
    pieChart.dispose();
    pieChart = null;
  }
  if (funnelChart) {
    funnelChart.dispose();
    funnelChart = null;
  }
});
</script>

<style scoped>
.range-bar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 16px;
}
.metrics-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
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
/* P1-3：指标口径说明（灰色小字） */
.metric-hint {
  font-size: 12px;
  color: #98a3b3;
  line-height: 1.5;
  margin-top: 6px;
  min-height: 36px;
}
/* P1-3：按期完成率卡——超期未闭环红色角标 */
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
/* 按期完成率 < 80% 标红预警 */
.on-time-low {
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
.chart {
  height: 320px;
  width: 100%;
}
.overdue-card {
  margin-top: 16px;
}
.overdue-card :deep(.el-card__header) {
  display: flex;
  align-items: center;
  gap: 8px;
}
.overdue-total-tag {
  margin-left: 4px;
}
.overdue-text {
  color: #e5652a;
  font-weight: 700;
}
.overdue-more {
  margin-top: 12px;
  text-align: center;
  font-size: 13px;
  color: #5b6b7d;
}
</style>
