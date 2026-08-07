<template>
  <div class="settings">
    <h2>{{ t("settings") }}</h2>
    <div class="section">
      <h3>{{ t("llmModel") }}</h3>
      <select v-model="model" @change="save">
        <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
      </select>
    </div>
    <div class="section">
      <h3>{{ t("datasource") }}</h3>
      <select v-model="datasource" @change="save">
        <option v-for="d in datasources" :key="d" :value="d">{{ d }}</option>
      </select>
    </div>
    <div class="section">
      <h3>{{ t("language") }}</h3>
      <select v-model="lang" @change="onLangChange">
        <option value="en">English</option>
        <option value="cn">简体中文</option>
      </select>
    </div>
  </div>
</template>
<script setup>
import { ref } from "vue";
import { useI18n } from "../utils/i18n.js";
const { locale, t } = useI18n();

const models = ["deepseek-chat", "gpt-4", "gpt-3.5-turbo"];
const datasources = ["doris", "mysql", "postgresql"];
const model = ref(localStorage.getItem("askinsight_model")||"deepseek-chat");
const datasource = ref(localStorage.getItem("askinsight_ds")||"doris");
const lang = ref(locale.value);

function save(){localStorage.setItem("askinsight_model",model.value);localStorage.setItem("askinsight_ds",datasource.value)}
function onLangChange(){locale.value = lang.value; save()}
</script>
<style scoped>
.settings{max-width:600px;margin:40px auto;padding:20px}
h2{font-size:24px;margin-bottom:24px}
.section{margin-bottom:20px}
h3{font-size:14px;color:#888;margin-bottom:6px}
select{padding:8px 12px;border:1px solid #ddd;border-radius:6px;font-size:14px;width:100%;max-width:300px}
</style>