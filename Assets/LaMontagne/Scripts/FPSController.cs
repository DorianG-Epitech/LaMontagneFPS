using UnityEngine;
using UnityEngine.InputSystem;

/// First-person controller: WASD / arrows, mouse look, Shift sprint, Space jump, Ctrl/C crouch.
[RequireComponent(typeof(CharacterController))]
public class FPSController : MonoBehaviour
{
    public Transform cameraPivot;
    public float walkSpeed = 4.3f, sprintSpeed = 7.8f, crouchSpeed = 2.1f;
    public float jumpHeight = 1.05f, gravity = -18f;
    public float mouseSensitivity = 0.085f;
    public float standHeight = 1.80f, crouchHeight = 1.05f, eyeBelowTop = 0.12f;
    public float groundAccel = 14f, airAccel = 2.5f;
    public bool inputEnabled = true;
    // scripted input (bot walker): used instead of devices when 'scripted' is true
    public bool scripted;
    public Vector2 scriptedMove, scriptedLook;
    public bool scriptedJump, scriptedSprint, scriptedCrouch;
    // touch input (TouchControls, mobile / WebGL): added to device input
    [System.NonSerialized] public Vector2 touchMove, touchLook;
    [System.NonSerialized] public bool touchJump, touchSprint, touchCrouch;

    public bool IsSprinting { get; private set; }
    public bool IsCrouching { get; private set; }
    public bool IsGrounded => cc != null && cc.isGrounded;
    public Vector3 Velocity => vel;
    public float Yaw => yaw;
    public float Pitch => pitch;

    CharacterController cc;
    Vector3 vel;
    float yaw, pitch, curH, bobT, recoilPitch, recoilYaw;

    void Awake()
    {
        cc = GetComponent<CharacterController>();
        yaw = transform.eulerAngles.y;
        curH = standHeight;
        ApplyHeight();
    }

    void Start()
    {
        if (Application.isFocused && Application.platform != RuntimePlatform.WebGLPlayer) { Cursor.lockState = CursorLockMode.Locked; Cursor.visible = false; }
    }

    public void AddRecoil(float up, float side) { recoilPitch += up; recoilYaw += side; }

    public void Teleport(Vector3 feet, float yawDeg, float pitchDeg)
    {
        cc.enabled = false;
        transform.position = feet;
        yaw = yawDeg; pitch = pitchDeg; vel = Vector3.zero;
        transform.rotation = Quaternion.Euler(0, yaw, 0);
        if (cameraPivot) cameraPivot.localRotation = Quaternion.Euler(pitch, 0, 0);
        cc.enabled = true;
    }

    void ApplyHeight()
    {
        cc.height = curH;
        cc.center = new Vector3(0, curH * 0.5f, 0);
    }

    void Update()
    {
        float dt = Time.deltaTime;
        var kb = Keyboard.current; var ms = Mouse.current; var gp = Gamepad.current;
        Vector2 look = Vector2.zero, move = Vector2.zero;
        bool jump = false, crouch = false, sprint = false;
        if (scripted)
        {
            move = scriptedMove; look = scriptedLook; jump = scriptedJump; sprint = scriptedSprint; crouch = scriptedCrouch;
            scriptedJump = false;
        }
        else if (inputEnabled)
        {
            if (ms != null && !TouchControls.Active)
            {
                if (Cursor.lockState == CursorLockMode.Locked) look += ms.delta.ReadValue() * mouseSensitivity;
                if (ms.leftButton.wasPressedThisFrame && Cursor.lockState != CursorLockMode.Locked) { Cursor.lockState = CursorLockMode.Locked; Cursor.visible = false; }
            }
            if (kb != null)
            {
                if (kb.wKey.isPressed || kb.upArrowKey.isPressed) move.y += 1;
                if (kb.sKey.isPressed || kb.downArrowKey.isPressed) move.y -= 1;
                if (kb.dKey.isPressed || kb.rightArrowKey.isPressed) move.x += 1;
                if (kb.aKey.isPressed || kb.leftArrowKey.isPressed) move.x -= 1;
                jump = kb.spaceKey.wasPressedThisFrame;
                crouch = kb.leftCtrlKey.isPressed || kb.cKey.isPressed;
                sprint = kb.leftShiftKey.isPressed;
                if (kb.escapeKey.wasPressedThisFrame) { Cursor.lockState = CursorLockMode.None; Cursor.visible = true; }
            }
            move += touchMove; look += touchLook; jump |= touchJump; sprint |= touchSprint; crouch |= touchCrouch;
            touchLook = Vector2.zero; touchJump = false;
            if (gp != null)
            {
                move += gp.leftStick.ReadValue();
                look += gp.rightStick.ReadValue() * 180f * dt;
                jump |= gp.buttonSouth.wasPressedThisFrame;
                crouch |= gp.buttonEast.isPressed;
                sprint |= gp.leftStickButton.isPressed;
            }
        }

        // look (+ recoil recovery)
        yaw += look.x + recoilYaw;
        pitch = Mathf.Clamp(pitch - look.y - recoilPitch, -89f, 89f);
        recoilPitch = 0; recoilYaw = 0;
        transform.rotation = Quaternion.Euler(0, yaw, 0);

        // crouch with head-room check
        float targetH = crouch ? crouchHeight : standHeight;
        if (!crouch && curH < standHeight - 0.01f)
        {
            Vector3 top = transform.position + Vector3.up * (curH - cc.radius);
            if (Physics.SphereCast(top, cc.radius * 0.9f, Vector3.up, out _, standHeight - curH + 0.05f, ~0, QueryTriggerInteraction.Ignore))
                targetH = curH;
        }
        curH = Mathf.MoveTowards(curH, targetH, dt * 5f);
        ApplyHeight();
        IsCrouching = curH < standHeight - 0.2f;

        if (move.sqrMagnitude > 1) move.Normalize();
        IsSprinting = sprint && move.y > 0.1f && !IsCrouching;
        float speed = IsCrouching ? crouchSpeed : (IsSprinting ? sprintSpeed : walkSpeed);
        Vector3 wish = (transform.right * move.x + transform.forward * move.y) * speed;

        bool grounded = cc.isGrounded;
        Vector3 hv = new Vector3(vel.x, 0, vel.z);
        hv = Vector3.Lerp(hv, wish, 1f - Mathf.Exp(-(grounded ? groundAccel : airAccel) * dt));
        if (grounded && vel.y < 0) vel.y = -2f;
        if (grounded && jump && !IsCrouching) vel.y = Mathf.Sqrt(2f * jumpHeight * -gravity);
        vel.y += gravity * dt;
        vel.x = hv.x; vel.z = hv.z;
        var flags = cc.Move(vel * dt);
        if ((flags & CollisionFlags.Above) != 0 && vel.y > 0) vel.y = 0;

        // head bob
        float hs = hv.magnitude;
        if (grounded && hs > 0.5f) bobT += dt * hs * 1.6f; else bobT = Mathf.Lerp(bobT, Mathf.Round(bobT / Mathf.PI) * Mathf.PI, dt * 4);
        float bob = Mathf.Sin(bobT * 2f) * 0.035f * Mathf.Clamp01(hs / 4f);
        if (cameraPivot)
        {
            cameraPivot.localPosition = new Vector3(Mathf.Cos(bobT) * 0.02f * Mathf.Clamp01(hs / 4f), curH - eyeBelowTop + bob, 0);
            cameraPivot.localRotation = Quaternion.Euler(pitch, 0, 0);
        }
    }
}
