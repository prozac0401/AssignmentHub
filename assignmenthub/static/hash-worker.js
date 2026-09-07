'use strict';
importScripts('/upload-static/sha256.js');
self.onmessage = async event => {
  const {file,id}=event.data;
  try {
    const hash=new SHA256();
    // Bounded 2 MiB reads, including over HTTP LAN without crypto.subtle.
    const step=2*1024*1024;
    for (let offset=0;offset<file.size;offset+=step) {
      hash.update(await file.slice(offset,Math.min(offset+step,file.size)).arrayBuffer());
      self.postMessage({id,bytes:Math.min(offset+step,file.size)});
    }
    self.postMessage({id,sha256:hash.digest(),bytes:file.size});
  } catch(error) {self.postMessage({id,error:String(error.message || error)});}
};
