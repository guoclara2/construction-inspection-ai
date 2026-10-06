// 应用入口：按使用组件注册 + Pinia + Router
import { createApp } from 'vue';
import { createPinia } from 'pinia';
import { ElAlert, ElAside, ElButton, ElCard, ElCheckbox, ElCheckboxGroup, ElCol, ElContainer, ElDatePicker, ElDescriptions, ElDescriptionsItem, ElDialog, ElDrawer, ElEmpty, ElForm, ElFormItem, ElHeader, ElImage, ElInput, ElInputNumber, ElMain, ElMenu, ElMenuItem, ElOption, ElPagination, ElRadio, ElRadioButton, ElRadioGroup, ElRow, ElSelect, ElSwitch, ElTabPane, ElTable, ElTableColumn, ElTabs, ElTag, ElTimeline, ElTimelineItem, ElUpload, ElLoading, ElConfigProvider } from 'element-plus';
import 'element-plus/dist/index.css';

import App from './App.vue';
import router from './router';

const app = createApp(App);
app.use(createPinia());
app.use(router);
for (const component of [ElAlert, ElAside, ElButton, ElCard, ElCheckbox, ElCheckboxGroup, ElCol, ElContainer, ElDatePicker, ElDescriptions, ElDescriptionsItem, ElDialog, ElDrawer, ElEmpty, ElForm, ElFormItem, ElHeader, ElImage, ElInput, ElInputNumber, ElMain, ElMenu, ElMenuItem, ElOption, ElPagination, ElRadio, ElRadioButton, ElRadioGroup, ElRow, ElSelect, ElSwitch, ElTabPane, ElTable, ElTableColumn, ElTabs, ElTag, ElTimeline, ElTimelineItem, ElUpload, ElConfigProvider]) app.use(component);
app.use(ElLoading);
app.mount('#app');
