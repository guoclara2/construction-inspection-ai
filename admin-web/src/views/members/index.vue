<template>
  <div>
    <!-- 项目信息卡 -->
    <el-card shadow="never" class="mb16">
      <template #header>
        <div class="card-head">
          <span>项目信息</span>
          <div>
            <!-- 组织管理员（org:manage）可新建项目 -->
            <el-button v-if="canManageOrg" type="success" size="small" @click="openCreateProjectDialog">新增项目</el-button>
            <el-button type="primary" size="small" @click="openProjectDialog">编辑</el-button>
          </div>
        </div>
      </template>
      <el-descriptions :column="3" border>
        <el-descriptions-item label="项目名称">{{ project.name || '—' }}</el-descriptions-item>
        <el-descriptions-item label="项目类型">{{ TYPE[project.project_type] || '—' }}</el-descriptions-item>
        <el-descriptions-item label="建设阶段">{{ PHASE[project.phase] || '—' }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- 成员管理 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <div class="head-left">
            <span>成员管理</span>
            <el-input v-model="keyword" placeholder="搜索姓名/用户名" clearable class="search-input"
              :prefix-icon="Search" />
          </div>
          <el-button type="primary" @click="openCreateDialog">新增成员</el-button>
        </div>
      </template>

      <!-- P2-3 小屏适配：用户名/姓名合并、未闭环工单数列头精简，核心列 656px 下无横向滚动 -->
      <el-table :data="filteredUsers" v-loading="loading" border stripe table-layout="auto">
        <el-table-column label="用户名 / 姓名" min-width="110" show-overflow-tooltip>
          <template #default="{ row }">
            <div class="cell-two-line">
              <div>{{ row.username }}</div>
              <div class="muted">{{ row.name }}</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="phone" label="手机号" min-width="105" show-overflow-tooltip>
          <template #default="{ row }">{{ row.phone || '—' }}</template>
        </el-table-column>
        <el-table-column label="角色" min-width="120">
          <template #default="{ row }">
            <el-tag v-for="r in (row.roles || [])" :key="r" :type="roleTagType(r)" class="role-tag">
              {{ ROLE[r] || r }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="专业" min-width="76">
          <template #default="{ row }">{{ row.specialty ? (SPECIALTY[row.specialty] || row.specialty) : '—' }}</template>
        </el-table-column>
        <el-table-column label="状态" min-width="64" align="center">
          <template #default="{ row }">
            <el-switch :model-value="row.active" :aria-label="'启用或停用成员 ' + row.name" @change="(val) => toggleActive(row, val)" />
          </template>
        </el-table-column>
        <el-table-column prop="open_count" label="未闭环单" min-width="72" align="center" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEditDialog(row)">编辑</el-button>
            <el-button link type="warning" @click="resetPassword(row)">重置密码</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination v-model:current-page="page" :page-size="pageSize" :total="total"
          layout="total, prev, pager, next" @current-change="loadUsers" />
      </div>
    </el-card>

    <!-- 项目编辑弹窗 -->
    <el-dialog v-model="projectDialog.visible" title="编辑项目信息" width="480px">
      <el-form :model="projectDialog.form" :rules="projectDialog.rules" ref="projectFormRef" label-width="90px">
        <el-form-item label="项目名称" prop="name">
          <el-input v-model="projectDialog.form.name" maxlength="100" />
        </el-form-item>
        <!-- 类型/阶段开放修改：项目进入新阶段后调整，事项推荐将按新类型/阶段匹配知识库 -->
        <el-form-item label="项目类型" prop="project_type">
          <el-select v-model="projectDialog.form.project_type" aria-label="项目类型" style="width: 100%">
            <el-option v-for="(label, value) in TYPE" :key="value" :label="label" :value="value" />
          </el-select>
        </el-form-item>
        <el-form-item label="建设阶段" prop="phase">
          <el-select v-model="projectDialog.form.phase" aria-label="建设阶段" style="width: 100%">
            <el-option v-for="(label, value) in PHASE" :key="value" :label="label" :value="value" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="projectDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="projectDialog.saving" @click="saveProject">保存</el-button>
      </template>
    </el-dialog>

    <!-- 新增项目弹窗（组织管理员） -->
    <el-dialog v-model="createProjectDialog.visible" title="新增项目" width="480px">
      <el-form :model="createProjectDialog.form" :rules="createProjectDialog.rules" ref="createFormRef" label-width="90px">
        <el-form-item label="项目名称" prop="name">
          <el-input v-model="createProjectDialog.form.name" maxlength="100" placeholder="请输入项目名称" />
        </el-form-item>
        <el-form-item label="项目类型" prop="project_type">
          <el-select v-model="createProjectDialog.form.project_type" aria-label="项目类型" placeholder="请选择项目类型" style="width: 100%">
            <el-option v-for="(label, value) in TYPE" :key="value" :label="label" :value="value" />
          </el-select>
        </el-form-item>
        <el-form-item label="建设阶段" prop="phase">
          <el-select v-model="createProjectDialog.form.phase" aria-label="建设阶段" placeholder="请选择建设阶段" style="width: 100%">
            <el-option v-for="(label, value) in PHASE" :key="value" :label="label" :value="value" />
          </el-select>
        </el-form-item>
        <el-form-item label="项目编码" prop="code">
          <el-input v-model="createProjectDialog.form.code" maxlength="20" placeholder="选填，用于工单号前缀" />
        </el-form-item>
        <el-form-item label="项目地址" prop="address">
          <el-input v-model="createProjectDialog.form.address" maxlength="200" placeholder="选填" />
        </el-form-item>
      </el-form>
      <div class="dialog-tip">新增项目归属于当前组织，创建后可在右上角项目切换中选择进入；项目成员需在进入该项目后单独添加。</div>
      <template #footer>
        <el-button @click="createProjectDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="createProjectDialog.saving" @click="saveCreateProject">创建</el-button>
      </template>
    </el-dialog>

    <!-- 成员新增/编辑弹窗 -->
    <el-dialog v-model="userDialog.visible" :title="userDialog.mode === 'create' ? '新增成员' : '编辑成员'" width="480px">
      <el-form :model="userDialog.form" :rules="userDialog.rules" ref="userFormRef" label-width="90px">
        <el-form-item v-if="userDialog.mode === 'create'" label="用户名" prop="username">
          <el-input v-model="userDialog.form.username" maxlength="50" placeholder="登录账号" />
        </el-form-item>
        <el-form-item label="姓名" prop="name">
          <el-input v-model="userDialog.form.name" maxlength="50" />
        </el-form-item>
        <el-form-item label="手机号" prop="phone">
          <el-input v-model="userDialog.form.phone" maxlength="20" placeholder="选填" />
        </el-form-item>
        <el-form-item label="角色" prop="role">
          <el-select v-model="userDialog.form.role" aria-label="角色" style="width: 100%">
            <el-option v-for="(label, value) in MEMBER_ROLE_OPTIONS" :key="value" :label="label" :value="value" />
          </el-select>
        </el-form-item>
        <el-form-item label="专业" prop="specialty">
          <el-select v-model="userDialog.form.specialty" aria-label="专业" style="width: 100%" clearable>
            <el-option v-for="(label, value) in SPECIALTY" :key="value" :label="label" :value="value" />
          </el-select>
        </el-form-item>
      </el-form>
      <div v-if="userDialog.mode === 'create'" class="dialog-tip">
        新增成员将由系统生成一次性强密码，保存后弹窗展示，请立即交予本人；首次登录需强制改密。
      </div>
      <template #footer>
        <el-button @click="userDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="userDialog.saving" @click="saveUser">保存</el-button>
      </template>
    </el-dialog>

    <!-- 一次性初始密码展示弹窗 -->
    <el-dialog v-model="pwdDialog.visible" title="初始密码（仅显示一次）" width="440px" :close-on-click-modal="false">
      <el-alert type="warning" :closable="false" show-icon
        title="该密码仅显示一次，请立即复制并交予本人。关闭后无法再次查看。" class="mb16" />
      <el-descriptions :column="1" border>
        <el-descriptions-item label="用户名">{{ pwdDialog.username }}</el-descriptions-item>
        <el-descriptions-item label="初始密码">
          <span class="pwd-text">{{ pwdDialog.password }}</span>
        </el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <el-button type="primary" @click="copyPassword">复制密码</el-button>
        <el-button @click="pwdDialog.visible = false">我已复制，关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Search } from '@element-plus/icons-vue';
import request from '../../utils/request';
import { useUserStore } from '../../stores/user';
import { ROLE, MEMBER_ROLE_OPTIONS, SPECIALTY, TYPE, PHASE } from '../../utils/dict';

const userStore = useUserStore();
// 组织级项目管理权限（org:manage）：组织管理员可新增项目
const canManageOrg = computed(() =>
  (userStore.userInfo && userStore.userInfo.perms ? userStore.userInfo.perms : []).includes('org:manage'));

// ---------- 项目信息 ----------
const project = reactive({ id: null, name: '', project_type: '', phase: '', status: '' });
const projectFormRef = ref(null);
const projectDialog = reactive({
  visible: false,
  saving: false,
  form: { name: '', project_type: '', phase: '' },
  rules: {
    name: [{ required: true, message: '请输入项目名称', trigger: 'blur' }],
    project_type: [{ required: true, message: '请选择项目类型', trigger: 'change' }],
    phase: [{ required: true, message: '请选择建设阶段', trigger: 'change' }],
  },
});

// ---------- 新增项目（组织管理员） ----------
const createFormRef = ref(null);
const createProjectDialog = reactive({
  visible: false,
  saving: false,
  form: { name: '', project_type: '', phase: '', code: '', address: '' },
  rules: {
    name: [{ required: true, message: '请输入项目名称', trigger: 'blur' }],
    project_type: [{ required: true, message: '请选择项目类型', trigger: 'change' }],
    phase: [{ required: true, message: '请选择建设阶段', trigger: 'change' }],
  },
});

function openCreateProjectDialog() {
  createProjectDialog.form = { name: '', project_type: '', phase: '', code: '', address: '' };
  createProjectDialog.visible = true;
}

function saveCreateProject() {
  createFormRef.value.validate((valid) => {
    if (!valid || createProjectDialog.saving) return;
    createProjectDialog.saving = true;
    request.post('/api/admin/projects', createProjectDialog.form).then(() => {
      createProjectDialog.visible = false;
      ElMessage.success('项目创建成功，可通过右上角项目切换进入');
      // 刷新可切换项目列表（顶部下拉数据源）
      refreshProjects();
    }).catch(() => {}).finally(() => {
      createProjectDialog.saving = false;
    });
  });
}

// 拉取最新用户信息（含 projects），同步到顶栏项目切换下拉
function refreshProjects() {
  request.get('/api/auth/me').then((data) => {
    if (data && data.id) {
      userStore.userInfo = data;
      localStorage.setItem('userInfo', JSON.stringify(data));
      if (Array.isArray(data.projects)) {
        userStore.projects = data.projects;
        localStorage.setItem('projects', JSON.stringify(data.projects));
      }
    }
  }).catch(() => {});
}

function loadProject() {
  request.get('/api/admin/project').then((data) => {
    Object.assign(project, data);
  }).catch(() => {});
}

function openProjectDialog() {
  projectDialog.form = { name: project.name, project_type: project.project_type, phase: project.phase };
  projectDialog.visible = true;
}

function saveProject() {
  projectFormRef.value.validate((valid) => {
    if (!valid || projectDialog.saving) return;
    projectDialog.saving = true;
    request.put('/api/admin/project', projectDialog.form).then((data) => {
      Object.assign(project, data);
      projectDialog.visible = false;
      ElMessage.success('项目信息已保存');
    }).catch(() => {}).finally(() => {
      projectDialog.saving = false;
    });
  });
}

// ---------- 成员管理 ----------
const users = ref([]);
const keyword = ref('');
const page = ref(1);
const pageSize = 20;
const total = ref(0);
const loading = ref(false);
const userFormRef = ref(null);

// 一次性密码弹窗
const pwdDialog = reactive({ visible: false, username: '', password: '' });

function showInitialPassword(username, password) {
  pwdDialog.username = username;
  pwdDialog.password = password;
  pwdDialog.visible = true;
}

function copyPassword() {
  const text = pwdDialog.password;
  const done = () => ElMessage.success('已复制到剪贴板');
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
}

function fallbackCopy(text, done) {
  const ta = document.createElement('textarea');
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); done(); } catch (e) { ElMessage.error('复制失败，请手动复制'); }
  document.body.removeChild(ta);
}

const userDialog = reactive({
  visible: false,
  mode: 'create',
  saving: false,
  userId: null,
  form: { username: '', name: '', phone: '', role: '', specialty: '' },
  rules: {
    username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
    name: [{ required: true, message: '请输入姓名', trigger: 'blur' }],
    role: [{ required: true, message: '请选择角色', trigger: 'change' }],
  },
});

// 搜索：name/username 前端模糊过滤（当前页数据）
const filteredUsers = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  if (!kw) return users.value;
  return users.value.filter(
    (u) => (u.name || '').toLowerCase().includes(kw) || (u.username || '').toLowerCase().includes(kw),
  );
});

function loadUsers() {
  loading.value = true;
  request.get('/api/admin/users', { params: { page: page.value, page_size: pageSize } })
    .then((data) => {
      users.value = data.list || [];
      total.value = data.total || 0;
    })
    .catch(() => {})
    .finally(() => {
      loading.value = false;
    });
}

function roleTagType(role) {
  return {
    project_admin: 'danger', org_admin: 'danger', inspector: 'primary',
    rectifier: 'success', reviewer: 'warning', viewer: 'info',
  }[role] || 'info';
}

function openCreateDialog() {
  userDialog.mode = 'create';
  userDialog.userId = null;
  userDialog.form = { username: '', name: '', phone: '', role: '', specialty: '' };
  userDialog.visible = true;
}

function openEditDialog(row) {
  userDialog.mode = 'edit';
  userDialog.userId = row.id;
  userDialog.form = { username: row.username, name: row.name, phone: row.phone || '', role: (row.roles && row.roles[0]) || '', specialty: row.specialty || '' };
  userDialog.visible = true;
}

function saveUser() {
  userFormRef.value.validate((valid) => {
    if (!valid || userDialog.saving) return;
    userDialog.saving = true;
    const isCreate = userDialog.mode === 'create';
    const url = isCreate ? '/api/admin/users' : '/api/admin/users/' + userDialog.userId;
    const body = {
      name: userDialog.form.name,
      phone: userDialog.form.phone || null,
      role: userDialog.form.role,
      specialty: userDialog.form.specialty || null,
    };
    const done = () => {
      userDialog.visible = false;
      ElMessage.success(isCreate ? '新增成功' : '已保存');
      loadUsers();
    };
    if (isCreate) {
      request.post(url, { ...body, username: userDialog.form.username }).then((data) => {
        done();
        // 展示一次性初始密码
        if (data && data.initial_password) {
          showInitialPassword(data.username, data.initial_password);
        }
      }).catch(() => {}).finally(() => {
        userDialog.saving = false;
      });
    } else {
      request.put(url, body).then(done).catch(() => {}).finally(() => {
        userDialog.saving = false;
      });
    }
  });
}

function resetPassword(row) {
  ElMessageBox.confirm(`确认重置「${row.name}」的密码？重置后该成员当前登录将失效，需用新密码首次登录并强制改密。`, '重置密码', {
    type: 'warning', confirmButtonText: '确认重置', cancelButtonText: '取消',
  }).then(() => {
    request.post('/api/admin/users/' + row.id + '/reset-password').then((data) => {
      if (data && data.initial_password) {
        showInitialPassword(data.username, data.initial_password);
      }
    }).catch(() => {});
  }).catch(() => {});
}

function toggleActive(row, val) {
  request.put('/api/admin/users/' + row.id, { active: val }).then(() => {
    row.active = val;
    ElMessage.success(val ? '已启用' : '已停用');
  }).catch(() => {
    ElMessageBox.alert('状态未变更', '提示');
  });
}

onMounted(() => {
  loadProject();
  loadUsers();
});
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
.head-left {
  display: flex;
  align-items: center;
  gap: 16px;
}
.search-input {
  width: 220px;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
.dialog-tip {
  font-size: 12px;
  color: #e6a23c;
  margin-left: 90px;
}
.role-tag {
  margin-right: 4px;
}
/* P2-3：合并单元格两行显示 */
.cell-two-line {
  line-height: 1.5;
  font-size: 12px;
}
.cell-two-line .muted,
.muted {
  color: #909399;
}
.pwd-text {
  font-family: monospace;
  font-size: 16px;
  font-weight: 700;
  color: #1e5fd0;
  letter-spacing: 1px;
}
</style>
