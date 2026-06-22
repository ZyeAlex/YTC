#!/usr/bin/env node
/**
 * 抖音 Web a_bogus 签名（仅供 listcollection 等接口使用）。
 * 用法: node douyin_abogus.js <url_query> [post_body] [user_agent] [window_env]
 */
"use strict";

function rc4_encrypt(plaintext, key) {
  const s = [];
  for (let i = 0; i < 256; i++) s[i] = i;
  let j = 0;
  for (let i = 0; i < 256; i++) {
    j = (j + s[i] + key.charCodeAt(i % key.length)) % 256;
    [s[i], s[j]] = [s[j], s[i]];
  }
  let i = 0;
  j = 0;
  const cipher = [];
  for (let k = 0; k < plaintext.length; k++) {
    i = (i + 1) % 256;
    j = (j + s[i]) % 256;
    [s[i], s[j]] = [s[j], s[i]];
    const t = (s[i] + s[j]) % 256;
    cipher.push(String.fromCharCode(s[t] ^ plaintext.charCodeAt(k)));
  }
  return cipher.join("");
}

function le(e, r) {
  r %= 32;
  return ((e << r) | (e >>> (32 - r))) >>> 0;
}

function de(e) {
  if (e >= 0 && e < 16) return 2043430169;
  if (e >= 16 && e < 64) return 2055708042;
  return 0;
}

function pe(e, r, t, n) {
  if (e >= 0 && e < 16) return (r ^ t ^ n) >>> 0;
  if (e >= 16 && e < 64) return ((r & t) | (r & n) | (t & n)) >>> 0;
  return 0;
}

function he(e, r, t, n) {
  if (e >= 0 && e < 16) return (r ^ t ^ n) >>> 0;
  if (e >= 16 && e < 64) return ((r & t) | (~r & n)) >>> 0;
  return 0;
}

function SM3() {
  this.reg = [];
  this.chunk = [];
  this.size = 0;
  this.reset();
}

SM3.prototype.reset = function () {
  this.reg = [1937774191, 1226093241, 388252375, 3666478592, 2842636476, 372324522, 3817729613, 2969243214];
  this.chunk = [];
  this.size = 0;
};

SM3.prototype.write = function (e) {
  const a =
    typeof e === "string"
      ? Array.from(
          encodeURIComponent(e).replace(/%([0-9A-F]{2})/g, (_, r) =>
            String.fromCharCode(parseInt(r, 16))
          ),
          (c) => c.charCodeAt(0)
        )
      : e;
  this.size += a.length;
  let f = 64 - this.chunk.length;
  if (a.length < f) {
    this.chunk = this.chunk.concat(a);
    return;
  }
  this.chunk = this.chunk.concat(a.slice(0, f));
  while (this.chunk.length >= 64) {
    this._compress(this.chunk.slice(0, 64));
    f < a.length ? (this.chunk = a.slice(f, Math.min(f + 64, a.length))) : (this.chunk = []);
    f += 64;
  }
};

SM3.prototype._fill = function () {
  const a = 8 * this.size;
  let f = this.chunk.push(128) % 64;
  if (64 - f < 8) f -= 64;
  while (f < 56) {
    this.chunk.push(0);
    f++;
  }
  for (let i = 0; i < 4; i++) this.chunk.push((Math.floor(a / 4294967296) >>> (8 * (3 - i))) & 255);
  for (let i = 0; i < 4; i++) this.chunk.push((a >>> (8 * (3 - i))) & 255);
};

SM3.prototype._compress = function (chunk) {
  if (chunk.length < 64) return;
  const f = (() => {
    const r = new Array(132);
    for (let i = 0; i < 16; i++) {
      r[i] = (chunk[4 * i] << 24) | (chunk[4 * i + 1] << 16) | (chunk[4 * i + 2] << 8) | chunk[4 * i + 3];
      r[i] >>>= 0;
    }
    for (let n = 16; n < 68; n++) {
      let a = r[n - 16] ^ r[n - 9] ^ le(r[n - 3], 15);
      a = (a ^ le(a, 15) ^ le(a, 23)) >>> 0;
      r[n] = (a ^ le(r[n - 13], 7) ^ r[n - 6]) >>> 0;
    }
    for (let n = 0; n < 64; n++) r[n + 68] = (r[n] ^ r[n + 4]) >>> 0;
    return r;
  })();
  const i = this.reg.slice(0);
  for (let c = 0; c < 64; c++) {
    let o = (le(i[0], 12) + i[4] + le(de(c), c)) >>> 0;
    o = le(o, 7);
    const s = (o ^ le(i[0], 12)) >>> 0;
    let u = pe(c, i[0], i[1], i[2]);
    u = (u + i[3] + s + f[c + 68]) >>> 0;
    let b = he(c, i[4], i[5], i[6]);
    b = (b + i[7] + o + f[c]) >>> 0;
    i[3] = i[2];
    i[2] = le(i[1], 9);
    i[1] = i[0];
    i[0] = u;
    i[7] = i[6];
    i[6] = le(i[5], 19);
    i[5] = i[4];
    i[4] = (b ^ le(b, 9) ^ le(b, 17)) >>> 0;
  }
  for (let l = 0; l < 8; l++) this.reg[l] = (this.reg[l] ^ i[l]) >>> 0;
};

SM3.prototype.sum = function (e) {
  if (e) {
    this.reset();
    this.write(e);
  }
  this._fill();
  for (let f = 0; f < this.chunk.length; f += 64) this._compress(this.chunk.slice(f, f + 64));
  const out = new Array(32);
  for (let f = 0; f < 8; f++) {
    let c = this.reg[f];
    out[4 * f + 3] = c & 255;
    c >>>= 8;
    out[4 * f + 2] = c & 255;
    c >>>= 8;
    out[4 * f + 1] = c & 255;
    c >>>= 8;
    out[4 * f] = c & 255;
  }
  this.reset();
  return out;
};

function result_encrypt(long_str, num = "s4") {
  const s_obj = {
    s3: "ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe",
    s4: "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe",
  };
  const constant = { 0: 16515072, 1: 258048, 2: 4032, str: s_obj[num] };
  let result = "";
  let lound = 0;
  let long_int = get_long_int(lound, long_str);
  for (let i = 0; i < (long_str.length / 3) * 4; i++) {
    if (Math.floor(i / 4) !== lound) {
      lound += 1;
      long_int = get_long_int(lound, long_str);
    }
    const key = i % 4;
    let temp_int;
    if (key === 0) temp_int = (long_int & constant[0]) >> 18;
    else if (key === 1) temp_int = (long_int & constant[1]) >> 12;
    else if (key === 2) temp_int = (long_int & constant[2]) >> 6;
    else temp_int = long_int & 63;
    result += constant.str.charAt(temp_int);
  }
  return result;
}

function get_long_int(round, long_str) {
  round *= 3;
  return (long_str.charCodeAt(round) << 16) | (long_str.charCodeAt(round + 1) << 8) | long_str.charCodeAt(round + 2);
}

function gener_random(random, option) {
  return [
    (random & 255 & 170) | (option[0] & 85),
    (random & 255 & 85) | (option[0] & 170),
    ((random >> 8) & 255 & 170) | (option[1] & 85),
    ((random >> 8) & 255 & 85) | (option[1] & 170),
  ];
}

function generate_rc4_bb_str(url_search_params, user_agent, window_env_str, suffix = "cus", Arguments = [0, 1, 14]) {
  const sm3 = new SM3();
  const start_time = Date.now();
  const url_search_params_list = sm3.sum(sm3.sum(url_search_params + suffix));
  const cus = sm3.sum(sm3.sum(suffix));
  const ua = sm3.sum(
    result_encrypt(
      rc4_encrypt(user_agent, String.fromCharCode(0.00390625, 1, Arguments[2])),
      "s3"
    )
  );
  const end_time = Date.now();
  const b = {
    8: 3,
    10: end_time,
    15: { aid: 6383, pageId: 6241 },
    16: start_time,
    18: 44,
    19: [1, 0, 1, 5],
  };
  b[20] = (b[16] >> 24) & 255;
  b[21] = (b[16] >> 16) & 255;
  b[22] = (b[16] >> 8) & 255;
  b[23] = b[16] & 255;
  b[24] = (b[16] / 256 / 256 / 256 / 256) >> 0;
  b[25] = (b[16] / 256 / 256 / 256 / 256 / 256) >> 0;
  b[26] = (Arguments[0] >> 24) & 255;
  b[27] = (Arguments[0] >> 16) & 255;
  b[28] = (Arguments[0] >> 8) & 255;
  b[29] = Arguments[0] & 255;
  b[30] = (Arguments[1] / 256) & 255;
  b[31] = Arguments[1] % 256;
  b[32] = (Arguments[1] >> 24) & 255;
  b[33] = (Arguments[1] >> 16) & 255;
  b[34] = (Arguments[2] >> 24) & 255;
  b[35] = (Arguments[2] >> 16) & 255;
  b[36] = (Arguments[2] >> 8) & 255;
  b[37] = Arguments[2] & 255;
  b[38] = url_search_params_list[21];
  b[39] = url_search_params_list[22];
  b[40] = cus[21];
  b[41] = cus[22];
  b[42] = ua[23];
  b[43] = ua[24];
  b[44] = (b[10] >> 24) & 255;
  b[45] = (b[10] >> 16) & 255;
  b[46] = (b[10] >> 8) & 255;
  b[47] = b[10] & 255;
  b[48] = b[8];
  b[49] = (b[10] / 256 / 256 / 256 / 256) >> 0;
  b[50] = (b[10] / 256 / 256 / 256 / 256 / 256) >> 0;
  b[51] = b[15].pageId;
  b[52] = (b[15].pageId >> 24) & 255;
  b[53] = (b[15].pageId >> 16) & 255;
  b[54] = (b[15].pageId >> 8) & 255;
  b[55] = b[15].pageId & 255;
  b[56] = b[15].aid;
  b[57] = b[15].aid & 255;
  b[58] = (b[15].aid >> 8) & 255;
  b[59] = (b[15].aid >> 16) & 255;
  b[60] = (b[15].aid >> 24) & 255;
  const window_env_list = Array.from(window_env_str, (ch) => ch.charCodeAt(0));
  b[64] = window_env_list.length;
  b[65] = b[64] & 255;
  b[66] = (b[64] >> 8) & 255;
  b[69] = 0;
  b[70] = b[69] & 255;
  b[71] = (b[69] >> 8) & 255;
  b[72] =
    b[18] ^
    b[20] ^
    b[26] ^
    b[30] ^
    b[38] ^
    b[40] ^
    b[42] ^
    b[21] ^
    b[27] ^
    b[31] ^
    b[35] ^
    b[39] ^
    b[41] ^
    b[43] ^
    b[22] ^
    b[28] ^
    b[32] ^
    b[36] ^
    b[23] ^
    b[29] ^
    b[33] ^
    b[37] ^
    b[44] ^
    b[45] ^
    b[46] ^
    b[47] ^
    b[48] ^
    b[49] ^
    b[50] ^
    b[24] ^
    b[25] ^
    b[52] ^
    b[53] ^
    b[54] ^
    b[55] ^
    b[57] ^
    b[58] ^
    b[59] ^
    b[60] ^
    b[65] ^
    b[66] ^
    b[70] ^
    b[71];
  let bb = [
    b[18], b[20], b[52], b[26], b[30], b[34], b[58], b[38], b[40], b[53], b[42], b[21], b[27], b[54], b[55], b[31],
    b[35], b[57], b[39], b[41], b[43], b[22], b[28], b[32], b[60], b[36], b[23], b[29], b[33], b[37], b[44], b[45],
    b[59], b[46], b[47], b[48], b[49], b[50], b[24], b[25], b[65], b[66], b[70], b[71],
  ];
  bb = bb.concat(window_env_list).concat(b[72]);
  return rc4_encrypt(String.fromCharCode(...bb), String.fromCharCode(121));
}

function generate_random_str() {
  let random_str_list = gener_random(Math.random() * 10000, [3, 45]);
  random_str_list = random_str_list.concat(gener_random(Math.random() * 10000, [1, 0]));
  random_str_list = random_str_list.concat(gener_random(Math.random() * 10000, [1, 5]));
  return String.fromCharCode(...random_str_list);
}

function sign(url_search_params, user_agent, window_env_str, arguments_ = [0, 1, 14]) {
  const result_str =
    generate_random_str() +
    generate_rc4_bb_str(url_search_params, user_agent, window_env_str, "cus", arguments_);
  return result_encrypt(result_str, "s4") + "=";
}

function main() {
  const query = process.argv[2] || "";
  const body = process.argv[3] || "";
  const userAgent =
    process.argv[4] ||
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36";
  const windowEnv =
    process.argv[5] || "1920|1080|1920|1080|0|30|0|0|1920|1080|1920|1080|1525|747|24|24|MacIntel";
  if (!query) {
    process.stderr.write("missing query\n");
    process.exit(2);
  }
  const signInput = body ? `${query}&${body}` : query;
  process.stdout.write(sign(signInput, userAgent, windowEnv, [0, 1, 14]));
}

if (require.main === module) {
  main();
}

module.exports = { sign };
