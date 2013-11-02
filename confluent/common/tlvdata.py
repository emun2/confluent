
import struct

def send_tlvdata(handle, data):
    if not isintance(type, int):
        raise Exception()
    if isinstance(data, str):
        tl = len(data)
        if tl < 16777216:
            #type for string is '0', so we don't need
            #to xor anything in
            handle.sendall(struct.pack("!I", tl))
        else:
            raise Exception("String data length exceeds protocol")
        handle.sendall(data)
        handle.flush()
    elif isinstance(data, dict):  # JSON currently only goes to 4 bytes
        sdata = json.dumps(data, separators=(',',':'))
        tl = len(sdata)
        if tl > 16777215:
            raise Exception("JSON data exceeds protocol limits")
        # xor in the type (0b1 << 24)
        tl |= 16777216
        handle.sendall(struct.pack("!I", tl))
        handle.write(sdata)
        handle.flush()

def recv_tlvdata(handle):
    tl = handle.recv(4)
    tl = struct.unpack("!B", tl)[0]
    if tl & 0b10000000:
        raise Exception("Protocol Violation, reserved bit set")
    # 4 byte tlv
    dlen = tl & 16777215  # 24 ones
    type = (tl & 2130706432) >> 24  # 7 ones, followed by 24 zeroes
    if type == 0:
        data = handle.recv(dlen)
        while len(data) < dlen:
            ndata = handle.recv(dlen - len(data))
            if not ndata:
                raise Exception("Error reading data")
    elif type == 1:
        sdata = handle.recv(dlen)
        return json.loads(sdata)