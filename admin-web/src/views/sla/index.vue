<template>
  <div>
    <!-- 规则说明 -->
    <el-alert type="info" :closable="false" show-icon class="mb16"
      title="SLA 时限规则：新建工单按「检查项默认 → 项目级精确 → 项目级风险 → 组织级 → 内置兜底」优先级取时限；时限写入工单即固化，修改规则只影响此后新建的工单。" />

    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>时限规则</span>
          <el-button type="primary" @click="openCreateDialog">新增规则</el-button>
        </div>
      </template>

      <el-table :data="rules" v-loading="loading" border stripe>
        <el-table-column label="作用范围" min-width="140">
          <template #default="{ row }">
            <el-tag v-if="row.project_id === null" type="info" effect="plain">组织级默认</el-tag>
            <el-tag v-else type="primary" effect="plain">{{ projectName(row.project_id) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="风险等级" min-width="90">
          <template #default="{ row }">
            <el-tag :type="RISK_TAG[row.risk_level]">{{ RISK[row.risk_level] || row.risk_level }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="问题类别" min-width="110">
          <template #default="{ row }">
            {{ row.defect_category ? (CATEGORY[row.defect_category] || row.defect_category) : '全部类别' }}
          </template>
        </el-table-column>
        <el-table-column label="整改时限" min-width="100" align="center">
          <template #default="{ row }">{{ row.sla_hours }} 小时</template>
        </el-table-column>
        <el-table-column label="临期提醒" min-width="100" align="center">
          <template #default="{ row }">{{ Math.round(row.warn_ratio * 100) }}% 时</template>
        </el-table-column>
        <el-table-column label="超时升级" min-width="130" align="center">
          <template #default="{ row }">
            {{ row.escalate_after_hours }} 小时 → {{ row.escalate_to_role ? (ROLE[row.escalate_to_role] || row.escalate_to_role) : '未设置' }}
          </template>
        </el-table-column>
        <el-table-column label="催办限流" min-width="100" align="center">
          <template #default="{ row }">{{ row.urge_interval_hours ? row.urge_interval_hours + ' 小时' : '默认 1 小时' }}</template>
        </el-table-column>
        <el-table-column label="启用" min-width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" min-width="140">
          <template #default="{ row }">{{ fmtTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEditDialog(row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新增/编辑弹窗 -->
    <el-dialog v-model="dialog.visible" :title="dialog.mode === 'create' ? '新增 SLA 规则' : '编辑 SLA 规则'" width="520px">
      <el-form :model="dialog.form" :rules="dialog.rules" ref="formRef" label-width="100px">
        <el-form-item label="作用范围" prop="scope">
          <el-radio-group v-model="dialog.form.scope">
            <el-radio value="org">组织级默认</el-radio>
            <el-radio value="project">项目级</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="dialog.form.scope === 'project'" label="项目" prop="project_id">
          <el-select v-model="dialog.form.project_id" :disabled="!isOrgAdmin" aria-label="适用项目" style="width: 100%"
            :placeholder="isOrgAdmin ? '选择项目' : '仅可配置当前项目'">
            <el-option v-for="p in projectOptions" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
          <div v-if="!isOrgAdmin" class="form-tip">非组织管理员仅可为当前项目配置规则</div>
        </el-form-item>
        <el-form-item label="风险等级" prop="risk_level">
          <el-select v-model="dialog.form.risk_level" aria-label="风险等级" style="width: 100%">
            <el-option v-for="(label, value) in RISK" :key="value" :label="label" :value="value" />
          </el-select>
        </el-form-item>
        <el-form-item label="问题类别" prop="defect_category">
          <el-select v-model="dialog.form.defect_category" aria-label="问题类别" style="width: 100%" clearable placeholder="全部类别">
            <el-option v-for="(label, value) in CATEGORY" :key="value" :label="label" :value="value" />
          </el-select>
        </el-form-item>
        <el-form-item label="整改时限" prop="sla_hours">
          <el-input-number v-model="dialog.form.sla_hours" :min="1" :max="8760" :step="1" step-strictly
            controls-position="right" style="width: 160px" />
          <span class="unit">小时</span>
        </el-form-item>
        <el-form-item label="临期提醒" prop="warn_ratio">
          <el-input-number v-model="dialog.form.warn_ratio" :min="0.05" :max="1" :step="0.05" :precision="2"
            controls-position="right" style="width: 160px" />
          <span class="unit">（时限占比，达到即提醒）</span>
        </el-form-item>
        <el-form-item label="超时升级" prop="escalate_after_hours">
          <el-input-number v-model="dialog.form.escalate_after_hours" :min="1" :max="8760" :step="1" step-strictly
            controls-position="right" style="width: 160px" />
          <span class="unit">小时后升级通知</span>
        </el-form-item>
        <el-form-item label="升级对象" prop="escalate_to_role">
          <el-select v-model="dialog.form.escalate_to_role" aria-label="超时升级通知对象" style="width: 100%" clearable placeholder="不升级">
            <el-option label="项目管理员" value="project_admin" />
            <el-option label="组织管理员" value="org_admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="催办限流" prop="urge_interval_hours">
          <el-input-number v-model="dialog.form.urge_interval_hours" :min="1" :max="168" :step="1" step-strictly
            controls-position="right" style="width: 160px" placeholder="默认 1" />
          <span class="unit">小时内不重复催办（空=默认 1 小时）</span>
        </el-form-item>
        <el-form-item label="启用" prop="enabled">
          <el-switch v-model="dialog.form.enabled" aria-label="启用该 SLA 规则" />
        </el-form-item>
      </el-form>
      <div class="dialog-tip">同一「范围 + 风险等级 + 问题类别」维度仅一条规则；时限一旦写入工单即固化，修改只影响新单。</div>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import request from '../../utils/request';
import { ROLE, RISK, CATEGORY, fmtTime } from '../../utils/dict';
import { useUserStore } from '../../stores/user';

const userStore = useUserStore();

const RISK_TAG = { high: 'danger', medium: 'warning', low: 'info' };

const rules = ref([]);
const loading = ref(false);
const formRef = ref(null);
const dialog = reactive({
  visible: false,
  mode: 'create',
  saving: false,
  ruleId: null,
  form: { scope: 'org', project_id: null, risk_level: 'medium', defect_category: null, sla_hours: 48,
    warn_ratio: 0.75, escalate_after_hours: 24, escalate_to_role: null, urge_interval_hours: null, enabled: true },
  rules: {
    risk_level: [{ required: true, message: '请选择风险等级', trigger: 'change' }],
    project_id: [{ required: true, message: '请选择项目', trigger: 'change' }],
    sla_hours: [{ required: true, message: '请输入整改时限', trigger: 'blur' }],
    warn_ratio: [{ required: true, message: '请输入临期提醒比例', trigger: 'blur' }],
    escalate_after_hours: [{ required: true, message: '请输入升级时限', trigger: 'blur' }],
  },
});

const isOrgAdmin = computed(() => !!(userStore.userInfo && userStore.userInfo.is_org_admin));
// 项目级规则可选项目：组织管理员可选本组织全部项目，其他人仅当前项目
const projectOptions = computed(() => {
  const projects = userStore.projects || [];
  if (isOrgAdmin.value) return projects;
  const pid = Number(userStore.projectId);
  return projects.filter((p) => p.id === pid);
});

function projectName(pid) {
  const p = (userStore.projects || []).find((x) => x.id === pid);
  return p ? p.name : `项目 #${pid}`;
}

function loadRules() {
  loading.value = true;
  request.get('/api/admin/sla-rules')
    .then((data) => {
      rules.value = data || [];
    })
    .catch(() => {})
    .finally(() => {
      loading.value = false;
    });
}

function openCreateDialog() {
  dialog.mode = 'create';
  dialog.ruleId = null;
  dialog.form = { scope: 'org', project_id: null, risk_level: 'medium', defect_category: null, sla_hours: 48,
    warn_ratio: 0.75, escalate_after_hours: 24, escalate_to_role: null, urge_interval_hours: null, enabled: true };
  dialog.visible = true;
}

function openEditDialog(row) {
  dialog.mode = 'edit';
  dialog.ruleId = row.id;
  dialog.form = {
    scope: row.project_id === null ? 'org' : 'project',
    project_id: row.project_id,
    risk_level: row.risk_level,
    defect_category: row.defect_category || null,
    sla_hours: row.sla_hours,
    warn_ratio: Number(row.warn_ratio),
    escalate_after_hours: row.escalate_after_hours,
    escalate_to_role: row.escalate_to_role || null,
    urge_interval_hours: row.urge_interval_hours || null,
    enabled: row.enabled,
  };
  dialog.visible = true;
}

function save() {
  formRef.value.validate((valid) => {
    if (!valid || dialog.saving) return;
    dialog.saving = true;
    const body = {
      project_id: dialog.form.scope === 'project' ? dialog.form.project_id : null,
      risk_level: dialog.form.risk_level,
      defect_category: dialog.form.defect_category || null,
      sla_hours: dialog.form.sla_hours,
      warn_ratio: dialog.form.warn_ratio,
      escalate_after_hours: dialog.form.escalate_after_hours,
      escalate_to_role: dialog.form.escalate_to_role || null,
      urge_interval_hours: dialog.form.urge_interval_hours || null,
      enabled: dialog.form.enabled,
    };
    const req = dialog.mode === 'create'
      ? request.post('/api/admin/sla-rules', body)
      : request.put('/api/admin/sla-rules/' + dialog.ruleId, body);
    req.then(() => {
      dialog.visible = false;
      ElMessage.success(dialog.mode === 'create' ? '规则已新增，仅影响此后新建的工单' : '规则已保存，仅影响此后新建的工单');
      loadRules();
    }).catch(() => {}).finally(() => {
      dialog.saving = false;
    });
  });
}

onMounted(loadRules);
</script>

<style scoped>
.mb16 {
  margin-bottom: 16px;
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.unit {
  margin-left: 8px;
  font-size: 13px;
  color: #5b6b7d;
}
.form-tip {
  font-size: 12px;
  color: #909399;
  line-height: 1.4;
  margin-top: 2px;
}
.dialog-tip {
  font-size: 12px;
  color: #e6a23c;
  margin-left: 100px;
}
</style>
