// Run: node tests/test_sha256.cjs. Bounded buffers; compares independent Node implementation.
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const SHA256=require('../assignmenthub/static/sha256.js');
let cases=0;
for(const length of [0,1,3,55,56,57,63,64,65,127,128,129,1000,1048576,8388631]) {
  const input=Buffer.alloc(length);for(let i=0;i<length;i++)input[i]=(i*31+17)%256;
  const expected=crypto.createHash('sha256').update(input).digest('hex');
  for(const step of [1,7,64,127,65536,2097152].filter(s=>length<1048576||s>=65536)) {
    const hash=new SHA256();for(let offset=0;offset<input.length;offset+=step)hash.update(input.subarray(offset,offset+step));
    assert.equal(hash.digest(),expected,`length=${length} step=${step}`);assert.equal(hash.digest(),expected);cases++;
  }
}
assert.equal(new SHA256().update(Buffer.from('abc')).digest(),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
const finished=new SHA256();finished.digest();assert.throws(()=>finished.update(Buffer.from('x')),/finalized/);
console.log(`PASS: ${cases+2} SHA-256 vector/chunk/finalization checks`);
