<template>
  <div class="login-container">
    <div class="login-card">
      <h2>Agents Stage</h2>
      <p>Sign in to continue</p>
      <a :href="loginUrl">
        <button class="google-btn">Sign in with Google</button>
      </a>
    </div>
  </div>
</template>

<script setup lang="ts">
const loginUrl = `${window.location.origin}/api/auth/login`
</script>

<style scoped>
.login-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: #f5f5f5;
}

.login-card {
  background: #fff;
  border-radius: 12px;
  padding: 48px 40px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
  text-align: center;
}

.login-card h2 {
  margin: 0 0 8px;
  font-size: 24px;
  color: #333;
}

.login-card p {
  margin: 0 0 24px;
  color: #888;
}

.google-btn {
  display: inline-block;
  padding: 12px 32px;
  font-size: 16px;
  font-weight: 500;
  color: #fff;
  background: #4285f4;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.2s;
}

.google-btn:hover {
  background: #3367d6;
}
</style>
